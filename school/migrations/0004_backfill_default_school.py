from django.db import migrations


def backfill_default_school(apps, schema_editor):
    School = apps.get_model('school', 'School')
    AdminProfile = apps.get_model('schooladmin', 'AdminProfile')
    Student = apps.get_model('students', 'Student')
    Enrollment = apps.get_model('students', 'Enrollment')
    Teacher = apps.get_model('teachers', 'Teacher')
    TeacherAllocation = apps.get_model('teachers', 'TeacherAllocation')
    NonTeachingStaff = apps.get_model('staff', 'NonTeachingStaff')
    Standard = apps.get_model('academics', 'Standard')
    Section = apps.get_model('academics', 'Section')
    ClassTeacher = apps.get_model('academics', 'ClassTeacher')
    AcademicYear = apps.get_model('school', 'AcademicYear')

    school = None
    admin_profile = AdminProfile.objects.select_related('school').filter(school__isnull=False).first()
    if admin_profile:
        school = admin_profile.school
    if school is None:
        school = School.objects.first()
    if school is None:
        return

    AcademicYear.objects.filter(school__isnull=True).update(school=school)
    Standard.objects.filter(school__isnull=True).update(school=school)
    Section.objects.filter(school__isnull=True).update(school=school)
    Student.objects.filter(school__isnull=True).update(school=school)
    Enrollment.objects.filter(school__isnull=True).update(school=school)
    Teacher.objects.filter(school__isnull=True).update(school=school)
    TeacherAllocation.objects.filter(school__isnull=True).update(school=school)
    ClassTeacher.objects.filter(school__isnull=True).update(school=school)
    NonTeachingStaff.objects.filter(school__isnull=True).update(school=school)


class Migration(migrations.Migration):

    dependencies = [
        ('school', '0003_institution_alter_school_options_academicyear_school_and_more'),
        ('academics', '0003_classteacher_school_section_school_standard_school'),
        ('students', '0008_enrollment_school_student_school'),
        ('teachers', '0011_teacher_school_teacherallocation_school'),
        ('staff', '0009_nonteachingstaff_school'),
        ('schooladmin', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_default_school, migrations.RunPython.noop),
    ]
