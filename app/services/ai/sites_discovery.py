"""
Découverte automatique des sites canoniques à partir des entités extraites
dans les classifications de messages.

Pipeline :
  1. Aggréger toutes les valeurs distinctes de `classifications.entities.sites`
     avec leur fréquence d'apparition
  2. Demander à Claude Sonnet de regrouper les variantes (orthographe,
     préfixes "ADS IDF", abréviations) en sites canoniques
  3. Filtrer le bruit ("le tampon", "la fosse" — pas un site mais une zone)
  4. Retourner une proposition JSON que l'humain valide ensuite

Aucun écrit en base ici : c'est purement un service de proposition.
La persistance est faite par le endpoint /admin/save-sites une fois validée.
"""

import json
import logging
from collections import Counter
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)


DISCOVERY_MODEL = "claude-sonnet-4-5"


SYSTEM_PROMPT = """Tu es analyste opérationnel pour ADS, entreprise française de \
collecte et évacuation de déchets en Île-de-France (PVC, ferraille, alu, gravats, \
bois, multi-sites).

Ton rôle : à partir d'une liste de noms de sites mentionnés dans les messages \
WhatsApp pro (extraits automatiquement par IA, donc bruités), produire une liste \
canonique des SITES OPÉRATIONNELS réels d'ADS.

Tu reçois la liste sous forme `nom_brut : occurrences`. Tu dois :

1. Regrouper les variantes orthographiques du même site sous un nom canonique \
unique. Exemples typiques :
   - "Le Plessis", "Le Plessis-Belleville", "Plessis Belleville", \
"ADS IDF LE PLESSIS BELLEVILLE", "ADS IDF Nord LE PLESSIS" → \
canonical_name "Le Plessis-Belleville"
   - "Saint Leu", "Saint-Leu", "ADS Saint-Leu" → "Saint-Leu"

2. Pour chaque site, deviner la région si évident :
   - Mention "IDF Nord" → "IDF Nord"
   - Mention "IDF Sud" → "IDF Sud"
   - Site situé à Dreux → "Eure-et-Loir" (28)
   - Sinon laisse null

3. Filtrer le BRUIT — éléments qui ne sont pas des sites opérationnels :
   - Pièces / éléments physiques : "tampon", "fosse", "regard", "vanne", "alvéole"
   - Adjectifs / verbes confondus
   - Noms de personnes
   - Régions trop génériques (ex: "Île-de-France" seul)
   Tu les listes séparément dans `noise` pour que l'humain confirme.

4. Si tu n'es pas sûr qu'un terme soit un site (ex: "Buc", "Bois d'Arcy" — \
sont-ce des sites ou des destinations de transport ?), tu les mets dans \
`uncertain` avec une note explicative.

Règles strictes :
- Réponse UNIQUEMENT en JSON conforme au schéma fourni. Aucun texte autour.
- Pas de ```markdown.
- Tu ne dois JAMAIS inventer un site qui n'apparaît pas dans la liste.
- Chaque variante brute reçue doit être présente dans EXACTEMENT UN des \
`sites[].aliases`, `noise`, ou `uncertain`.

Schéma JSON attendu :
{
  "sites": [
    {
      "canonical_name": "Le Plessis-Belleville",
      "region": "IDF Nord",
      "aliases": ["Le Plessis", "Plessis Belleville", "ADS IDF LE PLESSIS BELLEVILLE"],
      "total_occurrences": 1240,
      "notes": "Site principal IDF Nord d'après le volume de messages"
    }
  ],
  "noise": [
    {"name": "le tampon", "reason": "élément physique, pas un site"},
    {"name": "regard", "reason": "élément technique"}
  ],
  "uncertain": [
    {"name": "Buc", "reason": "peut être un site ou une destination de transport"}
  ]
}
"""


async def discover_sites(
    site_counts: dict[str, int],
    *,
    extra_context: str | None = None,
) -> dict[str, Any]:
    """
    Appelle Claude Sonnet pour proposer un regroupement.

    `site_counts` : {nom_brut: nombre_d_occurrences}
    `extra_context` : note libre additionnelle (par ex. liste connue de sites
                      officiels ADS) à inclure dans le prompt utilisateur.

    Retourne le JSON parsé (sites/noise/uncertain).
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY non configurée")
    if not site_counts:
        return {"sites": [], "noise": [], "uncertain": []}

    # Liste triée par occurrences décroissantes — les plus fréquents en premier
    ranked = sorted(site_counts.items(), key=lambda kv: kv[1], reverse=True)
    lines = [f"- {name!r} : {count}" for name, count in ranked]
    user_msg = (
        "Voici les noms de sites extraits des messages WhatsApp, avec leur "
        "nombre d'occurrences. Regroupe-les selon le schéma demandé.\n\n"
        + "\n".join(lines)
    )
    if extra_context:
        user_msg += f"\n\nContexte additionnel ADS :\n{extra_context}"

    # Sonnet a un context large : on peut envoyer jusqu'à ~2000 entrées sans souci
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=DISCOVERY_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
    finally:
        await client.close()

    # Validation minimale
    if not isinstance(result, dict):
        raise ValueError("Claude n'a pas retourné un objet JSON")
    result.setdefault("sites", [])
    result.setdefault("noise", [])
    result.setdefault("uncertain", [])
    return result


def aggregate_sites_from_classifications(
    classifications: list[dict],
) -> Counter[str]:
    """
    À partir d'une liste de rows `message_classifications`, agrège la fréquence
    de chaque nom de site brut trouvé dans entities.sites.

    Normalise légèrement : strip(), garde la casse originale pour que Claude
    voie les variantes telles qu'elles apparaissent.
    """
    counter: Counter[str] = Counter()
    for c in classifications:
        entities = c.get("entities") or {}
        sites = entities.get("sites") or []
        if not isinstance(sites, list):
            continue
        for s in sites:
            if isinstance(s, str):
                cleaned = s.strip()
                if cleaned:
                    counter[cleaned] += 1
    return counter
