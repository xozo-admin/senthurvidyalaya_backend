from django.db import models
from accounts.models import User

class AdminProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE,
        limit_choices_to={'user_type': 'admin'}
    )
    school = models.ForeignKey('school.School', on_delete=models.CASCADE)
