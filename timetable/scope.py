from academics.models import Section, Standard
from school.models import AcademicYear
from school.tenant import get_academic_year_by_identifier, get_active_academic_year, scope_queryset_for_user
from teachers.models import Teacher


def get_scoped_active_year(request):
    return get_active_academic_year(request)


def get_scoped_year_by_name(request, year_name):
    return get_academic_year_by_identifier(request, year_name)


def scoped_standards(request):
    return scope_queryset_for_user(Standard.objects.all(), request)


def scoped_sections(request):
    return scope_queryset_for_user(Section.objects.all(), request)


def scoped_teachers(request):
    return scope_queryset_for_user(Teacher.objects.all(), request)


def get_scoped_standard(request, class_name):
    try:
        return scoped_standards(request).get(name=str(class_name))
    except Standard.MultipleObjectsReturned as exc:
        raise Standard.DoesNotExist("Select a school before using this class.") from exc


def get_scoped_section(request, standard, section_name):
    return scoped_sections(request).get(standard=standard, name=str(section_name))
