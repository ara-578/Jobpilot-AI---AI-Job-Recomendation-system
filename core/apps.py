from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Import inside ready to avoid accessing models before apps are loaded
        from .taxonomy import load_initial_taxonomy
        load_initial_taxonomy()
        from . import cache_signals
