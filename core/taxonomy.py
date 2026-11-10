from django.db import models
import json
from pathlib import Path

class SkillTaxonomy(models.Model):
    """Canonical skill name with its aliases.
    The ``canonical`` field is the normalized representation used throughout the system.
    ``aliases`` holds a list of alternative spellings / synonyms.
    """
    canonical = models.CharField(max_length=120, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Skill Taxonomy"
        verbose_name_plural = "Skill Taxonomies"

    def __str__(self):
        return self.canonical

class TitleSynonym(models.Model):
    """Mapping of a job title to related synonyms.
    Allows flexible grouping of equivalent titles.
    """
    title = models.CharField(max_length=120, unique=True)
    synonyms = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Title Synonym"
        verbose_name_plural = "Title Synonyms"

    def __str__(self):
        return self.title

def load_initial_taxonomy(json_path: Path = Path(__file__).with_name('taxonomy_seed.json')):
    """Bulk‑load taxonomy entries from a JSON file.
    Expected JSON structure:
    {
        "skills": {"python": ["python"], "js": ["js", "javascript"]},
        "titles": {"python developer": ["python developer", "django developer"]}
    }
    """
    if not json_path.is_file():
        return
    data = json.load(json_path.open())
    for canonical, aliases in data.get('skills', {}).items():
        SkillTaxonomy.objects.update_or_create(
            canonical=canonical,
            defaults={'aliases': aliases}
        )
    for title, synonyms in data.get('titles', {}).items():
        TitleSynonym.objects.update_or_create(
            title=title,
            defaults={'synonyms': synonyms}
        )
