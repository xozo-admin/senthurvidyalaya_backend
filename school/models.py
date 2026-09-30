from django.db import models


class Institution(models.Model):
    name = models.CharField(max_length=150, unique=True)
    address = models.TextField(blank=True, default="")
    contact_phone = models.CharField(max_length=20, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    logo = models.ImageField(upload_to='institutions/logos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class School(models.Model):
    institution = models.ForeignKey(
        Institution,
        on_delete=models.CASCADE,
        related_name='schools',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True, null=True, blank=True)
    address = models.TextField(blank=True, default="")
    contact_phone = models.CharField(max_length=20, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    logo = models.ImageField(upload_to='schools/logos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    total_students = models.IntegerField(default=0)
    total_staffs = models.IntegerField(default=0)
    total_teachers = models.IntegerField(default=0)
    total_non_teaching = models.IntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class AcademicYear(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='academic_years',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=20, help_text="e.g. 2025-2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False, help_text="Set to True if this is the active year")

    class Meta:
        ordering = ['-start_date']
        constraints = [
            models.UniqueConstraint(fields=['school', 'name'], name='unique_academic_year_per_school')
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_current:
            queryset = AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk)
            if self.school_id:
                queryset = queryset.filter(school_id=self.school_id)
            else:
                queryset = queryset.filter(school__isnull=True)
            queryset.update(is_current=False)
        super(AcademicYear, self).save(*args, **kwargs)
