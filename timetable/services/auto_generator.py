from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time
from typing import Dict, Iterable, List, Tuple
import uuid

from django.db import transaction

from academics.models import ClassTeacher, Section, Standard
from school.models import AcademicYear
from subjects.models import Subject
from teachers.models import Teacher, TeacherAllocation
from timetable.models import ClassBreak, TimetableSlot

try:
    from ortools.sat.python import cp_model
except Exception:  # pragma: no cover - guarded at runtime
    cp_model = None


@dataclass(frozen=True)
class Slot:
    day: str
    period_no: int
    start_time: time
    end_time: time


class TimetableAutoGenerator:
    """
    Generates timetable slots for a section using CP-SAT.
    """

    def __init__(
        self,
        *,
        academic_year: AcademicYear,
        standard: Standard,
        section: Section,
        days: Iterable[str],
        day_period_counts: Dict[str, int],
        max_subject_periods_per_day: int,
        period_duration_minutes: int,
        first_period_start_time: time,
        subject_periods: Dict[str, int] | None = None,
        section_subject_teacher_ids: Dict[str, Dict[str, int]] | None = None,
        support_combined_class: bool = False,
        overwrite_existing: bool = False,
        solver_timeout_seconds: int = 20,
    ) -> None:
        if cp_model is None:
            raise RuntimeError("Google OR-Tools is not installed. Install 'ortools' in backend requirements.")

        self.academic_year = academic_year
        self.standard = standard
        self.section = section
        self.days = list(days)
        self.day_period_counts = day_period_counts
        self.max_subject_periods_per_day = max_subject_periods_per_day
        self.period_duration_minutes = period_duration_minutes
        self.first_period_start_time = first_period_start_time
        self.subject_periods_input = subject_periods or {}
        self.section_subject_teacher_ids = section_subject_teacher_ids or {}
        self.support_combined_class = support_combined_class
        self.overwrite_existing = overwrite_existing
        self.solver_timeout_seconds = solver_timeout_seconds

    @staticmethod
    def _overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        return a_start < b_end and a_end > b_start

    @staticmethod
    def _add_minutes(base: time, minutes: int) -> time:
        dt = datetime.combine(datetime.today(), base) + timedelta(minutes=minutes)
        return dt.time().replace(microsecond=0)

    def _build_slots(self) -> List[Slot]:
        class_breaks = sorted(
            ClassBreak.objects.filter(academic_year=self.academic_year, standard=self.standard),
            key=lambda b: b.start_time,
        )
        # Use explicit user-provided first period start-time; period counts drive slot creation.
        base_start_time = self.first_period_start_time

        slots: List[Slot] = []
        for day in self.days:
            periods_for_day = int(self.day_period_counts.get(day, 0))
            if periods_for_day <= 0:
                continue

            current_start = base_start_time
            for period_no in range(1, periods_for_day + 1):
                current_end = self._add_minutes(current_start, self.period_duration_minutes)

                # Shift period after break if overlap occurs, while preserving period duration.
                guard = 0
                while guard < 20:
                    overlapping_break = next(
                        (
                            br
                            for br in class_breaks
                            if self._overlaps(current_start, current_end, br.start_time, br.end_time)
                        ),
                        None,
                    )
                    if not overlapping_break:
                        break
                    current_start = overlapping_break.end_time
                    current_end = self._add_minutes(current_start, self.period_duration_minutes)
                    guard += 1

                slots.append(
                    Slot(
                        day=day,
                        period_no=period_no,
                        start_time=current_start,
                        end_time=current_end,
                    )
                )
                current_start = current_end
        return slots

    def _subject_targets(
        self, subjects: List[Subject], total_slots: int
    ) -> Tuple[Dict[int, int], set[int], Dict[int, int], set[int]]:
        subject_by_name = {s.name.strip().lower(): s for s in subjects}
        fixed_targets: Dict[int, int] = {}
        fixed_subject_ids: set[int] = set()
        flexible_fixed_subject_ids: set[int] = set()

        fixed_total = 0
        for raw_name, count in self.subject_periods_input.items():
            subject = subject_by_name.get(str(raw_name).strip().lower())
            if not subject:
                continue
            if count is None or str(count).strip() == "":
                continue
            safe_count = max(0, int(count))
            fixed_targets[subject.id] = safe_count
            fixed_subject_ids.add(subject.id)
            fixed_total += safe_count

        if fixed_total > total_slots:
            raise ValueError(
                f"Requested subject counts total ({fixed_total}) exceeds generated slots ({total_slots})."
            )

        auto_subjects = [s for s in subjects if s.id not in fixed_subject_ids]
        remaining = total_slots - fixed_total
        auto_desired: Dict[int, int] = {}
        if remaining > 0 and auto_subjects:
            base = remaining // len(auto_subjects)
            extra = remaining % len(auto_subjects)
            for idx, subject in enumerate(auto_subjects):
                auto_desired[subject.id] = base + (1 if idx < extra else 0)
        elif remaining > 0 and not auto_subjects:
            # All subjects have user-provided counts but totals are below required slots.
            # Auto-fill remaining slots only through subjects explicitly set to 0.
            zero_count_subjects = [s for s in subjects if fixed_targets.get(s.id, 0) == 0]
            if not zero_count_subjects:
                raise ValueError(
                    "Extra periods remain after fixed subject counts. "
                    "Set at least one subject count to 0 for auto-fill."
                )
            flexible_fixed_subject_ids = {s.id for s in zero_count_subjects}
            base = remaining // len(zero_count_subjects)
            extra = remaining % len(zero_count_subjects)
            for idx, subject in enumerate(zero_count_subjects):
                auto_desired[subject.id] = base + (1 if idx < extra else 0)

        return fixed_targets, fixed_subject_ids, auto_desired, flexible_fixed_subject_ids

    def _group_slots_by_day(self, slots: List[Slot]) -> Dict[str, List[Slot]]:
        grouped: Dict[str, List[Slot]] = {day: [] for day in self.days}
        for slot in slots:
            grouped[slot.day].append(slot)
        for day in grouped:
            grouped[day].sort(key=lambda s: s.period_no)
        return grouped

    def generate_and_save(self) -> Dict[str, object]:
        existing_for_section = TimetableSlot.objects.filter(
            academic_year=self.academic_year,
            section=self.section,
            day__in=self.days,
        )
        if existing_for_section.exists() and not self.overwrite_existing:
            raise ValueError(
                "Timetable already exists for selected day(s). Enable overwrite to regenerate."
            )

        subjects = list(Subject.objects.filter(standard=self.standard).order_by("name"))
        if not subjects:
            raise ValueError(f"No subjects found for Class {self.standard.name}.")

        allocations = list(
            TeacherAllocation.objects.filter(
                academic_year=self.academic_year,
                standard=self.standard,
                subject__in=subjects,
            ).select_related("teacher", "subject")
        )
        if not allocations:
            raise ValueError(
                f"No teacher allocations found for Class {self.standard.name} in {self.academic_year.name}."
            )

        subject_teacher_ids: Dict[int, List[int]] = {}
        for alloc in allocations:
            subject_teacher_ids.setdefault(alloc.subject_id, []).append(alloc.teacher_id)
        forced_teachers_for_subject = self.section_subject_teacher_ids.get(self.section.name, {})

        slots = self._build_slots()
        if not slots:
            raise ValueError("No teachable slots available after applying break constraints.")

        fixed_targets, fixed_subject_ids, auto_desired, flexible_fixed_subject_ids = self._subject_targets(
            subjects, total_slots=len(slots)
        )

        busy_entries = TimetableSlot.objects.filter(
            academic_year=self.academic_year,
            day__in=self.days,
        )
        if self.overwrite_existing:
            busy_entries = busy_entries.exclude(section=self.section)

        busy_teacher_at_slot: Dict[Tuple[str, int], set[int]] = {}
        for row in busy_entries.values("day", "period_no", "teacher_id"):
            teacher_id = row["teacher_id"]
            if not teacher_id:
                continue
            key = (row["day"], row["period_no"])
            busy_teacher_at_slot.setdefault(key, set()).add(teacher_id)

        # Optional first-period preference:
        # If class teacher has an allocated subject for this class and is free,
        # lock first period of each selected day to that subject+teacher.
        # If not feasible, skip and continue with normal generation.
        preferred_first_period: Tuple[int, int] | None = None
        class_teacher_record = (
            ClassTeacher.objects.filter(
                academic_year=self.academic_year,
                section=self.section,
            )
            .select_related("teacher")
            .first()
        )
        first_period_days = sorted({slot.day for slot in slots if slot.period_no == 1})
        if class_teacher_record and first_period_days:
            class_teacher_id = class_teacher_record.teacher_id
            class_teacher_subject_ids = sorted(
                {
                    alloc.subject_id
                    for alloc in allocations
                    if alloc.teacher_id == class_teacher_id
                }
            )
            teacher_busy_in_first_period = any(
                class_teacher_id in busy_teacher_at_slot.get((day, 1), set())
                for day in first_period_days
            )

            if class_teacher_subject_ids and not teacher_busy_in_first_period:
                required_first_slots = len(first_period_days)
                for subject_id in class_teacher_subject_ids:
                    # If subject has fixed weekly count, it must still allow all first-day placements.
                    if (
                        subject_id in fixed_subject_ids
                        and subject_id not in flexible_fixed_subject_ids
                        and fixed_targets.get(subject_id, 0) < required_first_slots
                    ):
                        continue
                    preferred_first_period = (subject_id, class_teacher_id)
                    break

        model = cp_model.CpModel()
        x: Dict[Tuple[int, str, int, int], cp_model.IntVar] = {}

        for slot_idx, slot in enumerate(slots):
            for subject in subjects:
                allowed_teachers = subject_teacher_ids.get(subject.id, [])
                forced_teacher_id = forced_teachers_for_subject.get(subject.name)
                if forced_teacher_id:
                    if forced_teacher_id not in allowed_teachers:
                        raise ValueError(
                            f"Teacher allocation missing: {subject.name} for Section {self.section.name}."
                        )
                    allowed_teachers = [forced_teacher_id]
                if not allowed_teachers:
                    continue
                blocked_teachers = busy_teacher_at_slot.get((slot.day, slot.period_no), set())
                for teacher_id in allowed_teachers:
                    if teacher_id in blocked_teachers:
                        continue
                    x[(slot_idx, slot.day, subject.id, teacher_id)] = model.NewBoolVar(
                        f"x_{slot_idx}_{slot.day}_{subject.id}_{teacher_id}"
                    )

        if preferred_first_period:
            preferred_subject_id, preferred_teacher_id = preferred_first_period
            preferred_first_vars: List[cp_model.IntVar] = []
            for slot_idx, slot in enumerate(slots):
                if slot.period_no != 1:
                    continue
                preferred_var = x.get((slot_idx, slot.day, preferred_subject_id, preferred_teacher_id))
                if preferred_var is None:
                    preferred_first_vars = []
                    break
                preferred_first_vars.append(preferred_var)

            # Apply only if feasible for all first periods; else fallback to default approach.
            if preferred_first_vars:
                for var in preferred_first_vars:
                    model.Add(var == 1)

        for slot_idx, slot in enumerate(slots):
            vars_in_slot = [
                var
                for (i, _d, _s, _t), var in x.items()
                if i == slot_idx
            ]
            if not vars_in_slot:
                raise ValueError(
                    f"No feasible teacher-subject combination for {slot.day} period {slot.period_no}."
                )
            model.Add(sum(vars_in_slot) == 1)

        subject_count_vars: Dict[int, cp_model.IntVar] = {}
        for subject in subjects:
            vars_for_subject = [
                var
                for (_i, _d, sid, _t), var in x.items()
                if sid == subject.id
            ]
            cnt = model.NewIntVar(0, len(slots), f"cnt_{subject.id}")
            model.Add(cnt == sum(vars_for_subject))
            subject_count_vars[subject.id] = cnt
            if subject.id in fixed_subject_ids:
                if subject.id in flexible_fixed_subject_ids:
                    model.Add(cnt >= fixed_targets.get(subject.id, 0))
                else:
                    model.Add(cnt == fixed_targets.get(subject.id, 0))

        # When all subjects were user-provided but totals were below required slots,
        # we treat provided counts as minimums and auto-fill extras.
        # Prevent concentrating all extra periods into a single subject by capping
        # max extra periods per subject to a balanced ceiling.
        if flexible_fixed_subject_ids:
            fixed_total = sum(fixed_targets.get(subject.id, 0) for subject in subjects)
            remaining_slots = len(slots) - fixed_total
            subject_n = len(flexible_fixed_subject_ids)
            if remaining_slots > 0 and subject_n > 0:
                max_extra_per_subject = (remaining_slots + subject_n - 1) // subject_n
                for subject in subjects:
                    if subject.id in flexible_fixed_subject_ids:
                        model.Add(
                            subject_count_vars[subject.id]
                            <= fixed_targets.get(subject.id, 0) + max_extra_per_subject
                        )

        for slot_idx, slot in enumerate(slots):
            teacher_to_vars: Dict[int, List[cp_model.IntVar]] = {}
            for (i, _d, _sid, teacher_id), var in x.items():
                if i != slot_idx:
                    continue
                teacher_to_vars.setdefault(teacher_id, []).append(var)

            for vars_for_teacher in teacher_to_vars.values():
                model.Add(sum(vars_for_teacher) <= 1)

        # Hard constraint: a subject must use the same teacher across all assigned slots.
        # If a subject is not assigned at all, no teacher is selected for it.
        teacher_subject_choice_vars: Dict[int, List[cp_model.IntVar]] = {}
        for subject in subjects:
            allowed_teachers = subject_teacher_ids.get(subject.id, [])
            if not allowed_teachers:
                continue

            y: Dict[int, cp_model.IntVar] = {}
            for teacher_id in allowed_teachers:
                y[teacher_id] = model.NewBoolVar(f"y_subject_{subject.id}_teacher_{teacher_id}")

                vars_for_subject_teacher = [
                    var
                    for (_i, _d, sid, tid), var in x.items()
                    if sid == subject.id and tid == teacher_id
                ]
                if vars_for_subject_teacher:
                    model.Add(sum(vars_for_subject_teacher) <= len(slots) * y[teacher_id])
                else:
                    model.Add(y[teacher_id] == 0)
                teacher_subject_choice_vars.setdefault(teacher_id, []).append(y[teacher_id])

            # At most one teacher per subject in the whole timetable.
            model.Add(sum(y.values()) <= 1)

            subject_vars = [
                var
                for (_i, _d, sid, _tid), var in x.items()
                if sid == subject.id
            ]
            if subject_vars:
                # If any slot uses this subject, exactly one teacher must be selected.
                model.Add(sum(subject_vars) <= len(slots) * sum(y.values()))
                # Prevent selecting a teacher for an unused subject.
                model.Add(sum(subject_vars) >= sum(y.values()))

        day_slot_groups = self._group_slots_by_day(slots)
        consecutive_penalties: List[cp_model.IntVar] = []
        same_time_repeat_penalties: List[cp_model.IntVar] = []

        z: Dict[Tuple[int, int], cp_model.IntVar] = {}
        for slot_idx, _slot in enumerate(slots):
            for subject in subjects:
                vars_for_slot_subject = [
                    var
                    for (i, _d, sid, _t), var in x.items()
                    if i == slot_idx and sid == subject.id
                ]
                z_var = model.NewBoolVar(f"z_{slot_idx}_{subject.id}")
                z[(slot_idx, subject.id)] = z_var
                if vars_for_slot_subject:
                    model.Add(z_var == sum(vars_for_slot_subject))
                else:
                    model.Add(z_var == 0)

        slot_index_map = {(slot.day, slot.period_no): idx for idx, slot in enumerate(slots)}
        slots_by_period_no: Dict[int, List[int]] = {}
        for idx, slot in enumerate(slots):
            slots_by_period_no.setdefault(slot.period_no, []).append(idx)

        # Soft daily rule: prefer each selected day to contain all "daily required" subjects at least once.
        # Subjects with explicit positive counts are controlled by weekly count constraints instead.
        daily_required_subjects = [
            subject
            for subject in subjects
            if not (subject.id in fixed_subject_ids and fixed_targets.get(subject.id, 0) > 0)
        ]

        missing_daily_required_penalties: List[cp_model.IntVar] = []
        for day, dslots in day_slot_groups.items():
            day_slot_indices = [slot_index_map[(day, slot.period_no)] for slot in dslots]
            for subject in daily_required_subjects:
                day_subject_sum = sum(z[(slot_idx, subject.id)] for slot_idx in day_slot_indices)
                missing_var = model.NewBoolVar(f"missing_daily_{day}_{subject.id}")
                model.Add(day_subject_sum + missing_var >= 1)
                missing_daily_required_penalties.append(missing_var)

        for day, dslots in day_slot_groups.items():
            if len(dslots) < 2:
                continue
            for pos in range(len(dslots) - 1):
                left = dslots[pos]
                right = dslots[pos + 1]
                left_idx = slot_index_map[(day, left.period_no)]
                right_idx = slot_index_map[(day, right.period_no)]
                for subject in subjects:
                    penalty = model.NewBoolVar(
                        f"cons_{day}_{left.period_no}_{right.period_no}_{subject.id}"
                    )
                    model.Add(penalty <= z[(left_idx, subject.id)])
                    model.Add(penalty <= z[(right_idx, subject.id)])
                    model.Add(
                        penalty
                        >= z[(left_idx, subject.id)] + z[(right_idx, subject.id)] - 1
                    )
                    consecutive_penalties.append(penalty)

        # Soft constraint: diversify subject timing across week.
        # Penalize same subject appearing in the same period_no on multiple days.
        for period_no, slot_indices in slots_by_period_no.items():
            if len(slot_indices) < 2:
                continue
            for i in range(len(slot_indices)):
                for j in range(i + 1, len(slot_indices)):
                    left_idx = slot_indices[i]
                    right_idx = slot_indices[j]
                    for subject in subjects:
                        repeat_penalty = model.NewBoolVar(
                            f"repeat_t_{period_no}_{left_idx}_{right_idx}_{subject.id}"
                        )
                        model.Add(repeat_penalty <= z[(left_idx, subject.id)])
                        model.Add(repeat_penalty <= z[(right_idx, subject.id)])
                        model.Add(
                            repeat_penalty
                            >= z[(left_idx, subject.id)] + z[(right_idx, subject.id)] - 1
                        )
                        same_time_repeat_penalties.append(repeat_penalty)

        balance_penalties: List[cp_model.IntVar] = []
        for subject_id, desired in auto_desired.items():
            dev = model.NewIntVar(0, len(slots), f"dev_{subject_id}")
            model.Add(dev >= subject_count_vars[subject_id] - desired)
            model.Add(dev >= desired - subject_count_vars[subject_id])
            balance_penalties.append(dev)

        # Soft preference: in same class timetable, avoid assigning one teacher to multiple subjects.
        # Allow it only when required by feasibility.
        multi_subject_teacher_penalties: List[cp_model.IntVar] = []
        for teacher_id, subject_choice_vars in teacher_subject_choice_vars.items():
            if len(subject_choice_vars) < 2:
                continue
            teacher_subject_count = model.NewIntVar(0, len(subject_choice_vars), f"teacher_subj_cnt_{teacher_id}")
            model.Add(teacher_subject_count == sum(subject_choice_vars))
            excess = model.NewIntVar(0, len(subject_choice_vars), f"teacher_subj_excess_{teacher_id}")
            model.Add(excess >= teacher_subject_count - 1)
            multi_subject_teacher_penalties.append(excess)

        objective_terms = []
        if missing_daily_required_penalties:
            # Prefer daily coverage, but do not make it infeasible.
            objective_terms.extend([p * 100 for p in missing_daily_required_penalties])
        if multi_subject_teacher_penalties:
            # Strongly prefer different teachers for different subjects in this class.
            objective_terms.extend([p * 200 for p in multi_subject_teacher_penalties])
        if consecutive_penalties:
            objective_terms.extend([p * 5 for p in consecutive_penalties])
        if same_time_repeat_penalties:
            objective_terms.extend([p * 3 for p in same_time_repeat_penalties])
        if balance_penalties:
            objective_terms.extend(balance_penalties)
        if objective_terms:
            model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.solver_timeout_seconds)
        solver.parameters.num_search_workers = 8

        status = solver.Solve(model)
        subject_map = {s.id: s for s in subjects}
        teacher_ids = {k[3] for k in x.keys()}
        teacher_map = {
            t.id: t
            for t in Teacher.objects.filter(id__in=teacher_ids)
        }

        final_rows: List[Dict[str, object]] = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (slot_idx, day, subject_id, teacher_id), var in x.items():
                if solver.Value(var) != 1:
                    continue
                slot = slots[slot_idx]
                final_rows.append(
                    {
                        "day": day,
                        "period_no": slot.period_no,
                        "start_time": slot.start_time,
                        "end_time": slot.end_time,
                        "subject": subject_map[subject_id],
                        "teacher": teacher_map[teacher_id],
                    }
                )
        else:
            # Fallback mode: if constrained solve fails, assign every slot using
            # any available teacher-subject pair for that slot.
            subject_ids_with_teachers = [
                s.id for s in subjects if subject_teacher_ids.get(s.id)
            ]
            if not subject_ids_with_teachers:
                raise ValueError("No feasible timetable found: no allocated teachers for subjects.")

            teacher_usage: Dict[int, int] = {}
            teacher_primary_subject: Dict[int, int] = {}
            subject_teacher_choice: Dict[int, int] = {}
            rotate_index = 0
            subject_count = len(subject_ids_with_teachers)

            for slot in slots:
                blocked = busy_teacher_at_slot.get((slot.day, slot.period_no), set())
                assigned = False

                for offset in range(subject_count):
                    sid = subject_ids_with_teachers[(rotate_index + offset) % subject_count]
                    subject_name = subject_map[sid].name
                    forced_teacher_id = forced_teachers_for_subject.get(subject_name)
                    if forced_teacher_id:
                        candidate_teachers = (
                            [forced_teacher_id]
                            if forced_teacher_id not in blocked
                            else []
                        )
                    else:
                        candidate_teachers = [
                            tid for tid in subject_teacher_ids.get(sid, [])
                            if tid not in blocked
                        ]
                    if not candidate_teachers:
                        continue

                    # Prefer previously chosen teacher for the same subject when available.
                    chosen_for_subject = subject_teacher_choice.get(sid)
                    if chosen_for_subject in candidate_teachers:
                        teacher_id = chosen_for_subject
                    else:
                        # Prefer teachers not yet used for another subject in this class.
                        # If none available, reuse as fallback.
                        teacher_id = min(
                            candidate_teachers,
                            key=lambda tid: (
                                0 if (tid not in teacher_primary_subject or teacher_primary_subject[tid] == sid) else 1,
                                teacher_usage.get(tid, 0),
                                tid,
                            ),
                        )

                    subject_teacher_choice.setdefault(sid, teacher_id)
                    teacher_primary_subject.setdefault(teacher_id, sid)
                    teacher_usage[teacher_id] = teacher_usage.get(teacher_id, 0) + 1
                    rotate_index = (rotate_index + offset + 1) % subject_count

                    final_rows.append(
                        {
                            "day": slot.day,
                            "period_no": slot.period_no,
                            "start_time": slot.start_time,
                            "end_time": slot.end_time,
                            "subject": subject_map[sid],
                            "teacher": teacher_map[teacher_id],
                        }
                    )
                    assigned = True
                    break

                if not assigned:
                    raise ValueError(
                        f"No feasible teacher available for {slot.day} period {slot.period_no}."
                    )

        with transaction.atomic():
            if self.overwrite_existing:
                existing_for_section.delete()

            saved_slots: List[TimetableSlot] = []
            for row in final_rows:
                slot, _created = TimetableSlot.objects.update_or_create(
                    academic_year=self.academic_year,
                    section=self.section,
                    day=row["day"],
                    period_no=row["period_no"],
                    defaults={
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "subject": row["subject"],
                        "teacher": row["teacher"],
                    },
                )
                saved_slots.append(slot)

        saved_slots.sort(key=lambda s: (s.day, s.period_no))
        timetable: Dict[str, List[Dict[str, str]]] = {day: [] for day in self.days}

        for slot in saved_slots:
            timetable[slot.day].append(
                {
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    "subject": slot.subject.name,
                    "teacher": f"{slot.teacher.name} ({slot.teacher.teacher_id})" if slot.teacher else "No Teacher",
                    "is_substitution": False,
                }
            )

        return {
            "class": f"{self.standard.name} - {self.section.name}",
            "year": self.academic_year.name,
            "days": self.days,
            "created_count": len(saved_slots),
            "timetable": timetable,
        }


class MultiSectionTimetableAutoGenerator:
    """
    Generates timetables for multiple sections of the same class in one solve.
    """

    def __init__(
        self,
        *,
        academic_year: AcademicYear,
        standard: Standard,
        sections: Iterable[Section],
        days: Iterable[str],
        day_period_counts: Dict[str, int],
        max_subject_periods_per_day: int,
        period_duration_minutes: int,
        first_period_start_time: time,
        subject_periods: Dict[str, int] | None = None,
        section_subject_teacher_ids: Dict[str, Dict[str, int]] | None = None,
        support_combined_class: bool = False,
        overwrite_existing: bool = False,
        solver_timeout_seconds: int = 20,
    ) -> None:
        if cp_model is None:
            raise RuntimeError("Google OR-Tools is not installed. Install 'ortools' in backend requirements.")

        self.academic_year = academic_year
        self.standard = standard
        self.sections = list(sections)
        if not self.sections:
            raise ValueError("No sections provided for multi-section timetable generation.")

        self.days = list(days)
        self.day_period_counts = day_period_counts
        self.max_subject_periods_per_day = max_subject_periods_per_day
        self.period_duration_minutes = period_duration_minutes
        self.first_period_start_time = first_period_start_time
        self.subject_periods_input = subject_periods or {}
        self.section_subject_teacher_ids = section_subject_teacher_ids or {}
        self.support_combined_class = support_combined_class
        self.overwrite_existing = overwrite_existing
        self.solver_timeout_seconds = solver_timeout_seconds

        # Reuse slot/target helpers from the single-section generator.
        self._helper = TimetableAutoGenerator(
            academic_year=self.academic_year,
            standard=self.standard,
            section=self.sections[0],
            days=self.days,
            day_period_counts=self.day_period_counts,
            max_subject_periods_per_day=self.max_subject_periods_per_day,
            period_duration_minutes=self.period_duration_minutes,
            first_period_start_time=self.first_period_start_time,
            subject_periods=self.subject_periods_input,
            section_subject_teacher_ids=self.section_subject_teacher_ids,
            support_combined_class=self.support_combined_class,
            overwrite_existing=self.overwrite_existing,
            solver_timeout_seconds=self.solver_timeout_seconds,
        )

    def _build_slots(self) -> List[Slot]:
        return self._helper._build_slots()

    def _subject_targets(
        self, subjects: List[Subject], total_slots: int
    ) -> Tuple[Dict[int, int], set[int], Dict[int, int], set[int]]:
        return self._helper._subject_targets(subjects, total_slots)

    def _group_slots_by_day(self, slots: List[Slot]) -> Dict[str, List[Slot]]:
        return self._helper._group_slots_by_day(slots)

    def generate_and_save(self) -> Dict[str, object]:
        existing_for_sections = TimetableSlot.objects.filter(
            academic_year=self.academic_year,
            section__in=self.sections,
            day__in=self.days,
        )
        if existing_for_sections.exists() and not self.overwrite_existing:
            section_names = ", ".join(sorted({s.name for s in self.sections}))
            raise ValueError(
                f"Timetable already exists for selected day(s) in sections: {section_names}. "
                "Enable overwrite to regenerate."
            )

        subjects = list(Subject.objects.filter(standard=self.standard).order_by("name"))
        if not subjects:
            raise ValueError(f"No subjects found for Class {self.standard.name}.")

        allocations = list(
            TeacherAllocation.objects.filter(
                academic_year=self.academic_year,
                standard=self.standard,
                subject__in=subjects,
            ).select_related("teacher", "subject")
        )
        if not allocations:
            raise ValueError(
                f"No teacher allocations found for Class {self.standard.name} in {self.academic_year.name}."
            )

        subject_teacher_ids: Dict[int, List[int]] = {}
        for alloc in allocations:
            subject_teacher_ids.setdefault(alloc.subject_id, []).append(alloc.teacher_id)

        forced_teachers_by_section: Dict[int, Dict[str, int]] = {}
        section_name_by_id = {section.id: section.name for section in self.sections}
        for section in self.sections:
            forced_teachers_by_section[section.id] = self.section_subject_teacher_ids.get(section.name, {})

        combined_subject_teacher: Dict[int, int] = {}
        if self.support_combined_class and len(self.sections) > 1:
            subject_name_to_id = {subject.name: subject.id for subject in subjects}
            for subject_name, subject_id in subject_name_to_id.items():
                teacher_ids: List[int] = []
                valid_for_all_sections = True
                for section in self.sections:
                    teacher_id = forced_teachers_by_section.get(section.id, {}).get(subject_name)
                    if not teacher_id:
                        valid_for_all_sections = False
                        break
                    teacher_ids.append(int(teacher_id))
                if valid_for_all_sections and len(set(teacher_ids)) == 1:
                    combined_subject_teacher[subject_id] = teacher_ids[0]

        slots = self._build_slots()
        if not slots:
            raise ValueError("No teachable slots available after applying break constraints.")

        fixed_targets, fixed_subject_ids, auto_desired, flexible_fixed_subject_ids = self._subject_targets(
            subjects, total_slots=len(slots)
        )

        busy_entries = TimetableSlot.objects.filter(
            academic_year=self.academic_year,
            day__in=self.days,
        )
        if self.overwrite_existing:
            busy_entries = busy_entries.exclude(section__in=self.sections)

        busy_teacher_at_slot: Dict[Tuple[str, int], set[int]] = {}
        for row in busy_entries.values("day", "period_no", "teacher_id"):
            teacher_id = row["teacher_id"]
            if not teacher_id:
                continue
            key = (row["day"], row["period_no"])
            busy_teacher_at_slot.setdefault(key, set()).add(teacher_id)

        preferred_first_period: Dict[int, Tuple[int, int]] = {}
        first_period_days = sorted({slot.day for slot in slots if slot.period_no == 1})
        if first_period_days:
            for section in self.sections:
                class_teacher_record = (
                    ClassTeacher.objects.filter(
                        academic_year=self.academic_year,
                        section=section,
                    )
                    .select_related("teacher")
                    .first()
                )
                if not class_teacher_record:
                    continue

                class_teacher_id = class_teacher_record.teacher_id
                class_teacher_subject_ids = sorted(
                    {
                        alloc.subject_id
                        for alloc in allocations
                        if alloc.teacher_id == class_teacher_id
                    }
                )
                teacher_busy_in_first_period = any(
                    class_teacher_id in busy_teacher_at_slot.get((day, 1), set())
                    for day in first_period_days
                )

                if class_teacher_subject_ids and not teacher_busy_in_first_period:
                    required_first_slots = len(first_period_days)
                    for subject_id in class_teacher_subject_ids:
                        if (
                            subject_id in fixed_subject_ids
                            and subject_id not in flexible_fixed_subject_ids
                            and fixed_targets.get(subject_id, 0) < required_first_slots
                        ):
                            continue
                        preferred_first_period[section.id] = (subject_id, class_teacher_id)
                        break

        model = cp_model.CpModel()
        x: Dict[Tuple[int, int, str, int, int], cp_model.IntVar] = {}

        for section in self.sections:
            for slot_idx, slot in enumerate(slots):
                for subject in subjects:
                    allowed_teachers = subject_teacher_ids.get(subject.id, [])
                    forced_teacher_id = forced_teachers_by_section[section.id].get(subject.name)
                    if forced_teacher_id:
                        if forced_teacher_id not in allowed_teachers:
                            raise ValueError(
                                f"Teacher allocation missing: {subject.name} for Section {section.name}."
                            )
                        allowed_teachers = [forced_teacher_id]
                    if not allowed_teachers:
                        continue
                    blocked_teachers = busy_teacher_at_slot.get((slot.day, slot.period_no), set())
                    for teacher_id in allowed_teachers:
                        if teacher_id in blocked_teachers:
                            continue
                        x[(section.id, slot_idx, slot.day, subject.id, teacher_id)] = model.NewBoolVar(
                            f"x_{section.id}_{slot_idx}_{slot.day}_{subject.id}_{teacher_id}"
                        )

        for section in self.sections:
            preferred = preferred_first_period.get(section.id)
            if not preferred:
                continue
            preferred_subject_id, preferred_teacher_id = preferred
            preferred_first_vars: List[cp_model.IntVar] = []
            for slot_idx, slot in enumerate(slots):
                if slot.period_no != 1:
                    continue
                preferred_var = x.get(
                    (section.id, slot_idx, slot.day, preferred_subject_id, preferred_teacher_id)
                )
                if preferred_var is None:
                    preferred_first_vars = []
                    break
                preferred_first_vars.append(preferred_var)
            if preferred_first_vars:
                for var in preferred_first_vars:
                    model.Add(var == 1)

        if combined_subject_teacher:
            for slot_idx, slot in enumerate(slots):
                for subject_id, teacher_id in combined_subject_teacher.items():
                    vars_for_group: List[cp_model.IntVar] = []
                    for section in self.sections:
                        group_var = x.get((section.id, slot_idx, slot.day, subject_id, teacher_id))
                        if group_var is None:
                            vars_for_group = []
                            break
                        vars_for_group.append(group_var)
                    if len(vars_for_group) != len(self.sections):
                        for section in self.sections:
                            group_var = x.get((section.id, slot_idx, slot.day, subject_id, teacher_id))
                            if group_var is not None:
                                model.Add(group_var == 0)
                        continue
                    for idx in range(1, len(vars_for_group)):
                        model.Add(vars_for_group[idx] == vars_for_group[0])

        for section in self.sections:
            for slot_idx, slot in enumerate(slots):
                vars_in_slot = [
                    var
                    for (sid, i, _d, _s, _t), var in x.items()
                    if sid == section.id and i == slot_idx
                ]
                if not vars_in_slot:
                    raise ValueError(
                        f"No feasible teacher-subject combination for {section.name} "
                        f"{slot.day} period {slot.period_no}."
                    )
                model.Add(sum(vars_in_slot) == 1)

        subject_count_vars: Dict[Tuple[int, int], cp_model.IntVar] = {}
        for section in self.sections:
            for subject in subjects:
                vars_for_subject = [
                    var
                    for (sid, _i, _d, sub_id, _t), var in x.items()
                    if sid == section.id and sub_id == subject.id
                ]
                cnt = model.NewIntVar(0, len(slots), f"cnt_{section.id}_{subject.id}")
                model.Add(cnt == sum(vars_for_subject))
                subject_count_vars[(section.id, subject.id)] = cnt
                if subject.id in fixed_subject_ids:
                    if subject.id in flexible_fixed_subject_ids:
                        model.Add(cnt >= fixed_targets.get(subject.id, 0))
                    else:
                        model.Add(cnt == fixed_targets.get(subject.id, 0))

        if flexible_fixed_subject_ids:
            fixed_total = sum(fixed_targets.get(subject.id, 0) for subject in subjects)
            remaining_slots = len(slots) - fixed_total
            subject_n = len(flexible_fixed_subject_ids)
            if remaining_slots > 0 and subject_n > 0:
                max_extra_per_subject = (remaining_slots + subject_n - 1) // subject_n
                for section in self.sections:
                    for subject in subjects:
                        if subject.id in flexible_fixed_subject_ids:
                            model.Add(
                                subject_count_vars[(section.id, subject.id)]
                                <= fixed_targets.get(subject.id, 0) + max_extra_per_subject
                            )

        # Prevent teacher slot clashes; optionally allow same subject+teacher combined class across sections.
        for slot_idx, slot in enumerate(slots):
            teacher_to_vars: Dict[int, List[Tuple[int, cp_model.IntVar]]] = {}
            for (_sid, i, _d, sub_id, teacher_id), var in x.items():
                if i != slot_idx:
                    continue
                teacher_to_vars.setdefault(teacher_id, []).append((sub_id, var))

            for teacher_id, subject_var_pairs in teacher_to_vars.items():
                if not combined_subject_teacher:
                    model.Add(sum(var for _sub_id, var in subject_var_pairs) <= 1)
                    continue

                combined_rep_vars: List[cp_model.IntVar] = []
                combined_subject_ids_for_teacher = {
                    subject_id
                    for subject_id, combined_teacher_id in combined_subject_teacher.items()
                    if combined_teacher_id == teacher_id
                }

                for subject_id in combined_subject_ids_for_teacher:
                    rep_var = x.get((self.sections[0].id, slot_idx, slot.day, subject_id, teacher_id))
                    if rep_var is not None:
                        combined_rep_vars.append(rep_var)

                normal_vars = [
                    var
                    for subject_id, var in subject_var_pairs
                    if subject_id not in combined_subject_ids_for_teacher
                ]
                model.Add(sum(normal_vars) + sum(combined_rep_vars) <= 1)

        teacher_subject_choice_vars: Dict[Tuple[int, int], List[cp_model.IntVar]] = {}
        for section in self.sections:
            for subject in subjects:
                allowed_teachers = subject_teacher_ids.get(subject.id, [])
                if not allowed_teachers:
                    continue

                y: Dict[int, cp_model.IntVar] = {}
                for teacher_id in allowed_teachers:
                    y[teacher_id] = model.NewBoolVar(
                        f"y_subject_{section.id}_{subject.id}_teacher_{teacher_id}"
                    )

                    vars_for_subject_teacher = [
                        var
                        for (sid, _i, _d, sub_id, tid), var in x.items()
                        if sid == section.id and sub_id == subject.id and tid == teacher_id
                    ]
                    if vars_for_subject_teacher:
                        model.Add(sum(vars_for_subject_teacher) <= len(slots) * y[teacher_id])
                    else:
                        model.Add(y[teacher_id] == 0)
                    teacher_subject_choice_vars.setdefault((section.id, teacher_id), []).append(
                        y[teacher_id]
                    )

                model.Add(sum(y.values()) <= 1)

                subject_vars = [
                    var
                    for (sid, _i, _d, sub_id, _tid), var in x.items()
                    if sid == section.id and sub_id == subject.id
                ]
                if subject_vars:
                    model.Add(sum(subject_vars) <= len(slots) * sum(y.values()))
                    model.Add(sum(subject_vars) >= sum(y.values()))

        day_slot_groups = self._group_slots_by_day(slots)
        consecutive_penalties: List[cp_model.IntVar] = []
        same_time_repeat_penalties: List[cp_model.IntVar] = []
        missing_daily_required_penalties: List[cp_model.IntVar] = []
        balance_penalties: List[cp_model.IntVar] = []
        multi_subject_teacher_penalties: List[cp_model.IntVar] = []

        z: Dict[Tuple[int, int, int], cp_model.IntVar] = {}
        for section in self.sections:
            for slot_idx, _slot in enumerate(slots):
                for subject in subjects:
                    vars_for_slot_subject = [
                        var
                        for (sid, i, _d, sub_id, _t), var in x.items()
                        if sid == section.id and i == slot_idx and sub_id == subject.id
                    ]
                    z_var = model.NewBoolVar(f"z_{section.id}_{slot_idx}_{subject.id}")
                    z[(section.id, slot_idx, subject.id)] = z_var
                    if vars_for_slot_subject:
                        model.Add(z_var == sum(vars_for_slot_subject))
                    else:
                        model.Add(z_var == 0)

        slot_index_map = {(slot.day, slot.period_no): idx for idx, slot in enumerate(slots)}
        slots_by_period_no: Dict[int, List[int]] = {}
        for idx, slot in enumerate(slots):
            slots_by_period_no.setdefault(slot.period_no, []).append(idx)

        daily_required_subjects = [
            subject
            for subject in subjects
            if not (subject.id in fixed_subject_ids and fixed_targets.get(subject.id, 0) > 0)
        ]

        for section in self.sections:
            for day, dslots in day_slot_groups.items():
                day_slot_indices = [slot_index_map[(day, slot.period_no)] for slot in dslots]
                for subject in daily_required_subjects:
                    day_subject_sum = sum(
                        z[(section.id, slot_idx, subject.id)] for slot_idx in day_slot_indices
                    )
                    missing_var = model.NewBoolVar(f"missing_daily_{section.id}_{day}_{subject.id}")
                    model.Add(day_subject_sum + missing_var >= 1)
                    missing_daily_required_penalties.append(missing_var)

        for section in self.sections:
            for day, dslots in day_slot_groups.items():
                if len(dslots) < 2:
                    continue
                for pos in range(len(dslots) - 1):
                    left = dslots[pos]
                    right = dslots[pos + 1]
                    left_idx = slot_index_map[(day, left.period_no)]
                    right_idx = slot_index_map[(day, right.period_no)]
                    for subject in subjects:
                        penalty = model.NewBoolVar(
                            f"cons_{section.id}_{day}_{left.period_no}_{right.period_no}_{subject.id}"
                        )
                        model.Add(penalty <= z[(section.id, left_idx, subject.id)])
                        model.Add(penalty <= z[(section.id, right_idx, subject.id)])
                        model.Add(
                            penalty
                            >= z[(section.id, left_idx, subject.id)]
                            + z[(section.id, right_idx, subject.id)]
                            - 1
                        )
                        consecutive_penalties.append(penalty)

        for section in self.sections:
            for period_no, slot_indices in slots_by_period_no.items():
                if len(slot_indices) < 2:
                    continue
                for i in range(len(slot_indices)):
                    for j in range(i + 1, len(slot_indices)):
                        left_idx = slot_indices[i]
                        right_idx = slot_indices[j]
                        for subject in subjects:
                            repeat_penalty = model.NewBoolVar(
                                f"repeat_t_{section.id}_{period_no}_{left_idx}_{right_idx}_{subject.id}"
                            )
                            model.Add(repeat_penalty <= z[(section.id, left_idx, subject.id)])
                            model.Add(repeat_penalty <= z[(section.id, right_idx, subject.id)])
                            model.Add(
                                repeat_penalty
                                >= z[(section.id, left_idx, subject.id)]
                                + z[(section.id, right_idx, subject.id)]
                                - 1
                            )
                            same_time_repeat_penalties.append(repeat_penalty)

        for section in self.sections:
            for subject_id, desired in auto_desired.items():
                dev = model.NewIntVar(0, len(slots), f"dev_{section.id}_{subject_id}")
                model.Add(dev >= subject_count_vars[(section.id, subject_id)] - desired)
                model.Add(dev >= desired - subject_count_vars[(section.id, subject_id)])
                balance_penalties.append(dev)

        for (section_id, teacher_id), subject_choice_vars in teacher_subject_choice_vars.items():
            if len(subject_choice_vars) < 2:
                continue
            teacher_subject_count = model.NewIntVar(
                0, len(subject_choice_vars), f"teacher_subj_cnt_{section_id}_{teacher_id}"
            )
            model.Add(teacher_subject_count == sum(subject_choice_vars))
            excess = model.NewIntVar(
                0, len(subject_choice_vars), f"teacher_subj_excess_{section_id}_{teacher_id}"
            )
            model.Add(excess >= teacher_subject_count - 1)
            multi_subject_teacher_penalties.append(excess)

        objective_terms = []
        if missing_daily_required_penalties:
            objective_terms.extend([p * 100 for p in missing_daily_required_penalties])
        if multi_subject_teacher_penalties:
            objective_terms.extend([p * 200 for p in multi_subject_teacher_penalties])
        if consecutive_penalties:
            objective_terms.extend([p * 5 for p in consecutive_penalties])
        if same_time_repeat_penalties:
            objective_terms.extend([p * 3 for p in same_time_repeat_penalties])
        if balance_penalties:
            objective_terms.extend(balance_penalties)
        if objective_terms:
            model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.solver_timeout_seconds)
        solver.parameters.num_search_workers = 8

        status = solver.Solve(model)
        subject_map = {s.id: s for s in subjects}
        teacher_ids = {k[4] for k in x.keys()}
        teacher_map = {
            t.id: t
            for t in Teacher.objects.filter(id__in=teacher_ids)
        }

        final_rows: List[Dict[str, object]] = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (section_id, slot_idx, day, subject_id, teacher_id), var in x.items():
                if solver.Value(var) != 1:
                    continue
                slot = slots[slot_idx]
                final_rows.append(
                    {
                        "section_id": section_id,
                        "day": day,
                        "period_no": slot.period_no,
                        "start_time": slot.start_time,
                        "end_time": slot.end_time,
                        "subject": subject_map[subject_id],
                        "teacher": teacher_map[teacher_id],
                    }
                )
        else:
            subject_ids_with_teachers = [
                s.id for s in subjects if subject_teacher_ids.get(s.id)
            ]
            if not subject_ids_with_teachers:
                raise ValueError("No feasible timetable found: no allocated teachers for subjects.")

            assigned_teachers_at_slot: Dict[Tuple[str, int], set[int]] = {
                key: set(value) for key, value in busy_teacher_at_slot.items()
            }
            rotate_index_by_section: Dict[int, int] = {s.id: 0 for s in self.sections}
            subject_count = len(subject_ids_with_teachers)

            for section in self.sections:
                teacher_usage: Dict[int, int] = {}
                teacher_primary_subject: Dict[int, int] = {}
                subject_teacher_choice: Dict[int, int] = {}

            for slot in slots:
                blocked = assigned_teachers_at_slot.get((slot.day, slot.period_no), set())
                assigned = False

                rotate_index = rotate_index_by_section[section.id]
                for offset in range(subject_count):
                    sid = subject_ids_with_teachers[(rotate_index + offset) % subject_count]
                    subject_name = subject_map[sid].name
                    forced_teacher_id = forced_teachers_by_section[section.id].get(subject_name)
                    if forced_teacher_id:
                        candidate_teachers = (
                            [forced_teacher_id]
                            if forced_teacher_id not in blocked
                            else []
                        )
                    else:
                        candidate_teachers = [
                            tid for tid in subject_teacher_ids.get(sid, [])
                            if tid not in blocked
                        ]
                    if not candidate_teachers:
                        continue

                        chosen_for_subject = subject_teacher_choice.get(sid)
                        if chosen_for_subject in candidate_teachers:
                            teacher_id = chosen_for_subject
                        else:
                            teacher_id = min(
                                candidate_teachers,
                                key=lambda tid: (
                                    0
                                    if (tid not in teacher_primary_subject or teacher_primary_subject[tid] == sid)
                                    else 1,
                                    teacher_usage.get(tid, 0),
                                    tid,
                                ),
                            )

                        subject_teacher_choice.setdefault(sid, teacher_id)
                        teacher_primary_subject.setdefault(teacher_id, sid)
                        teacher_usage[teacher_id] = teacher_usage.get(teacher_id, 0) + 1
                        rotate_index_by_section[section.id] = (rotate_index + offset + 1) % subject_count
                        assigned_teachers_at_slot.setdefault((slot.day, slot.period_no), set()).add(teacher_id)

                        final_rows.append(
                            {
                                "section_id": section.id,
                                "day": slot.day,
                                "period_no": slot.period_no,
                                "start_time": slot.start_time,
                                "end_time": slot.end_time,
                                "subject": subject_map[sid],
                                "teacher": teacher_map[teacher_id],
                            }
                        )
                        assigned = True
                        break

                    if not assigned:
                        raise ValueError(
                            f"No feasible teacher available for {section.name} "
                            f"{slot.day} period {slot.period_no}."
                        )

        section_by_id = {section.id: section for section in self.sections}

        if self.support_combined_class and len(self.sections) > 1:
            grouped_rows: Dict[Tuple[str, int, int, int], List[Dict[str, object]]] = {}
            for row in final_rows:
                subject_obj = row["subject"]
                teacher_obj = row["teacher"]
                if not subject_obj or not teacher_obj:
                    continue
                key = (row["day"], row["period_no"], subject_obj.id, teacher_obj.id)
                grouped_rows.setdefault(key, []).append(row)

            for rows in grouped_rows.values():
                if len(rows) > 1:
                    group_id = str(uuid.uuid4())
                    for row in rows:
                        row["combined_group"] = group_id

        with transaction.atomic():
            if self.overwrite_existing:
                existing_for_sections.delete()

            saved_slots: List[TimetableSlot] = []
            for row in final_rows:
                section = section_by_id[row["section_id"]]
                slot, _created = TimetableSlot.objects.update_or_create(
                    academic_year=self.academic_year,
                    section=section,
                    day=row["day"],
                    period_no=row["period_no"],
                    defaults={
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "subject": row["subject"],
                        "teacher": row["teacher"],
                        "combined_group": row.get("combined_group"),
                    },
                )
                saved_slots.append(slot)

        saved_slots.sort(key=lambda s: (s.section_id, s.day, s.period_no))
        timetable_by_section: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        for section in self.sections:
            timetable_by_section[section.name] = {day: [] for day in self.days}

        for slot in saved_slots:
            section_name = slot.section.name
            timetable_by_section[section_name][slot.day].append(
                {
                    "period": slot.period_no,
                    "time": f"{slot.start_time} - {slot.end_time}",
                    "subject": slot.subject.name,
                    "teacher": f"{slot.teacher.name} ({slot.teacher.teacher_id})" if slot.teacher else "No Teacher",
                    "is_substitution": False,
                }
            )

        return {
            "class": f"{self.standard.name}",
            "sections": [s.name for s in self.sections],
            "year": self.academic_year.name,
            "days": self.days,
            "created_count": len(saved_slots),
            "timetables": timetable_by_section,
        }
