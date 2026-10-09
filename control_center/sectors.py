"""StartHere industry vocabulary, using one primary sector per deal."""
VERSION = "starthere-industry-2026-09-v4"
SECTORS = {
    "ai_applications": "AI Applications",
    "ai_infrastructure": "AI Infrastructure",
    "data_analytics": "Data & Analytics",
    "developer_tools": "Developer Tools",
    "cloud_infrastructure": "Cloud & Infrastructure",
    "cybersecurity": "Cybersecurity",
    "robotics_automation": "Robotics & Automation",
    "fintech": "Fintech",
    "healthtech_life_sciences": "Healthtech & Life Sciences",
    "climate_energy": "Climate & Energy",
    "industrial_manufacturing": "Industrial & Manufacturing",
    "construction_proptech": "Construction & Proptech",
    "commerce_marketplaces": "Commerce & Marketplaces",
    "consumer": "Consumer",
    "education": "Education",
    "media_gaming": "Media & Gaming",
    "transportation_logistics": "Transportation & Logistics",
    "aerospace_defense_public_safety": "Aerospace, Defense & Public Safety",
    "b2b_saas": "B2B SaaS",
    "crypto_blockchain": "Crypto & Blockchain",
}
PRIMARY_PRIORITY = (
    "healthtech_life_sciences", "fintech", "construction_proptech", "climate_energy",
    "transportation_logistics", "aerospace_defense_public_safety", "education", "media_gaming",
    "commerce_marketplaces", "consumer", "industrial_manufacturing", "robotics_automation",
    "cybersecurity", "developer_tools", "cloud_infrastructure", "data_analytics",
    "crypto_blockchain", "ai_infrastructure", "ai_applications", "b2b_saas",
)
GUIDANCE = """Sector describes the company's actual business, independently of fund fit. Use exactly one primary StartHere sector, never Outside Mandate or Adjacent Built World. Prefer a specific customer industry over generic AI or SaaS when both are materially evidenced. AI Infrastructure covers models, training/inference infrastructure and AI compute; AI Applications covers applied AI products; Developer Tools covers software-development tooling; Data & Analytics covers data platforms/analysis; Cloud & Infrastructure covers cloud, hosting and networking. Fintech includes payments, banking, lending, insurance and financial software. Construction & Proptech includes construction, architecture/BIM, real-estate and property technology. Climate & Energy covers energy generation/storage, emissions and environmental technology. Use Robotics & Automation for robotics/automation businesses; Industrial & Manufacturing for industrial production, materials and hardware. Select other verticals from actual products/customers. For otherwise equally supported labels, use StartHere's primary-sector priority: """ + ", ".join(SECTORS[key] for key in PRIMARY_PRIORITY) + ". Sector is not proof of mandate relevance; retain separate evidence-based Fit decisions."


def normalize_sector(value):
    value = str(value or "").strip()
    aliases = {key.casefold(): label for key, label in SECTORS.items()}
    aliases.update({label.casefold(): label for label in SECTORS.values()})
    return aliases.get(value.casefold(), value)
