from django.db import models
from subjects.models import Subject
from academics.models import Standard, Section
from teachers.models import Teacher
from students.models import Enrollment
from school.models import AcademicYear

# --- 1. EXAM TERM ---
class ExamTerm(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='exam_terms')

 # CHANGED: Removed unique=True
    name = models.CharField(max_length=50) 
    rank = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['rank']
        # ADDED: Make name unique only within a specific year
        unique_together = ('academic_year', 'name')

    def __str__(self):
        # CHANGED: Better display in Admin panel
        return f"{self.name} ({self.academic_year.name})"

# --- 2. EXAM TYPE ---
class ExamType(models.Model):
    term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name='exams')
    name = models.CharField(max_length=50) 
    max_marks = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)
    rank = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['rank']
        unique_together = ('term', 'name') 

    def __str__(self):
        term_name = self.term.name if self.term else "No Term"
        return f"{self.name} ({term_name})"

# --- 3. EXAM SCHEDULE ---
class ExamSchedule(models.Model):
    exam_type = models.ForeignKey(ExamType, on_delete=models.CASCADE)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='exam_schedules')
    start_date = models.DateField()
    end_date = models.DateField()
    classes = models.ManyToManyField(Standard, related_name='exam_schedules')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        # unique_together = ['exam_type', 'academic_year']

    def __str__(self):
        return f"{self.exam_type.name} - {self.academic_year.name}"

class ExamDetail(models.Model):
    schedule = models.ForeignKey(ExamSchedule, on_delete=models.CASCADE, related_name='details')
    subject_name = models.CharField(max_length=100)
    exam_date = models.DateField()
    duration = models.CharField(max_length=50)
    session = models.CharField(max_length=20)

# --- 4. STUDENT MARKS ---
class StudentMark(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='marks')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    # CHANGED: Replaced exam_type with schedule
    schedule = models.ForeignKey(ExamSchedule, on_delete=models.CASCADE, related_name='marks')
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=2)
    total_marks = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        unique_together = ('enrollment', 'subject', 'schedule') 

    @property
    def grade_point(self):
        if self.total_marks == 0: return 'F'
        percentage = (self.marks_obtained / self.total_marks) * 100
        if percentage >= 91: return 'S'
        elif percentage >= 81: return 'A'
        elif percentage >= 71: return 'B'
        elif percentage >= 61: return 'C'
        elif percentage >= 51: return 'D'
        elif percentage >= 40: return 'E'
        else: return 'F'

# --- 5. MARK CHANGE REQUEST (FINAL STRICT VERSION) ---
class MarkChangeRequest(models.Model):
    # FINAL CHANGE: 'enrollment' is Required, 'student' is Deleted
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)
    
    schedule = models.ForeignKey(ExamSchedule, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    requested_by = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    old_marks = models.DecimalField(max_digits=5, decimal_places=2)
    new_marks = models.DecimalField(max_digits=5, decimal_places=2)
    reason = models.TextField(default="Correction needed")
    status = models.CharField(
        max_length=20, 
        choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
        default='PENDING'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Req: {self.enrollment.student.student_name} - {self.subject.name}"


# ==========================================
# 6. CLASS TESTS (Teacher Created Assessment)
# ==========================================
class ClassTest(models.Model):
    # Link to the context
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE)
    term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE)
    
    # Link to the Creator/Target
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    section = models.ForeignKey(Section, on_delete=models.CASCADE) # Section implies Class (Standard)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    
    test_name = models.CharField(max_length=100) # e.g. "Cycle Test 1"
    max_marks = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent duplicate test names for the same subject/section in a term
        # e.g., You can't have two "Cycle Test 1"s for 10-A Maths in Term 1
        unique_together = ('academic_year', 'term', 'section', 'subject', 'test_name')

    def __str__(self):
        return f"{self.test_name} - {self.subject.name} ({self.section})"

class ClassTestMarks(models.Model):
    class_test = models.ForeignKey(ClassTest, on_delete=models.CASCADE, related_name='marks')
    
    # CORRECTED: Use Enrollment to bind this mark to the specific Academic Year
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)
    
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=2)
    
    class Meta:
        # One student can only have one mark entry per test
        unique_together = ('class_test', 'enrollment')

    def __str__(self):
        return f"{self.enrollment.student.student_name}: {self.marks_obtained}"