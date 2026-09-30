from django.apps import AppConfig

class TeachersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'teachers'
    
    # DELETE the ready() function and the import line below it.
    # We do not need it here anymore because 'accounts' is handling everything.