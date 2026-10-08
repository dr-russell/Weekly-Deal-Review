"""Deterministic location formatting; never infer a missing location."""
import re


def aliases(rows):
    return {alias.casefold(): name for name, *variants in rows for alias in (name, *variants)}


COUNTRIES = aliases([
    ("United States", "US", "USA", "U.S.", "U.S.A.", "United States of America"),
    ("United Kingdom", "UK", "GB", "GBR", "U.K.", "Great Britain"),
    ("Canada", "CA", "CAN"), ("Australia", "AU", "AUS"),
    ("India", "IN", "IND"), ("France", "FR", "FRA"),
    ("Germany", "DE", "DEU"), ("Israel", "IL", "ISR"),
    ("Netherlands", "NL", "NLD", "The Netherlands"),
    ("Switzerland", "CH", "CHE"), ("Italy", "IT", "ITA"),
    ("Sweden", "SE", "SWE"), ("Singapore", "SG", "SGP"),
    ("China", "CN", "CHN", "PRC", "People's Republic of China"),
    ("Belgium", "BE", "BEL"), ("Finland", "FI", "FIN"),
    ("Spain", "ES", "ESP"), ("Norway", "NO", "NOR"),
    ("Denmark", "DK", "DNK"), ("Japan", "JP", "JPN"),
    ("United Arab Emirates", "AE", "ARE", "UAE", "U.A.E."),
    ("Portugal", "PT", "PRT"), ("Saudi Arabia", "SA", "SAU"),
    ("Iceland", "IS", "ISL"), ("Brazil", "BR", "BRA"),
    ("Kenya", "KE", "KEN"), ("Lithuania", "LT", "LTU"),
    ("New Zealand", "NZ", "NZL"), ("Estonia", "EE", "EST"),
    ("Egypt", "EG", "EGY"), ("Luxembourg", "LU", "LUX"),
    ("Turkey", "TR", "TUR", "Türkiye"), ("Malaysia", "MY", "MYS"),
    ("South Korea", "KR", "KOR", "Republic of Korea"),
    ("Indonesia", "ID", "IDN"), ("Venezuela", "VE", "VEN"),
    ("Bulgaria", "BG", "BGR"), ("Jersey", "JE", "JEY"),
    ("Greece", "GR", "GRC"), ("Ukraine", "UA", "UKR"),
    ("Chile", "CL", "CHL"), ("Costa Rica", "CR", "CRI"),
    ("Colombia", "CO", "COL"), ("Mexico", "MX", "MEX"),
    ("Nigeria", "NG", "NGA"), ("Jordan", "JO", "JOR"),
    ("Seychelles", "SC", "SYC"), ("Ireland", "IE", "IRL"),
    ("South Africa", "ZA", "ZAF"), ("Poland", "PL", "POL"),
    ("Austria", "AT", "AUT"), ("Czechia", "CZ", "CZE", "Czech Republic"),
    ("Taiwan", "TW", "TWN"), ("Hong Kong", "HK", "HKG"),
])
US_STATES = aliases([
    ("Alabama", "AL"), ("Alaska", "AK"), ("Arizona", "AZ"), ("Arkansas", "AR"),
    ("California", "CA"), ("Colorado", "CO"), ("Connecticut", "CT"), ("Delaware", "DE"),
    ("Florida", "FL"), ("Georgia", "GA"), ("Hawaii", "HI"), ("Idaho", "ID"),
    ("Illinois", "IL"), ("Indiana", "IN"), ("Iowa", "IA"), ("Kansas", "KS"),
    ("Kentucky", "KY"), ("Louisiana", "LA"), ("Maine", "ME"), ("Maryland", "MD"),
    ("Massachusetts", "MA"), ("Michigan", "MI"), ("Minnesota", "MN"), ("Mississippi", "MS"),
    ("Missouri", "MO"), ("Montana", "MT"), ("Nebraska", "NE"), ("Nevada", "NV"),
    ("New Hampshire", "NH"), ("New Jersey", "NJ"), ("New Mexico", "NM"), ("New York", "NY"),
    ("North Carolina", "NC"), ("North Dakota", "ND"), ("Ohio", "OH"), ("Oklahoma", "OK"),
    ("Oregon", "OR"), ("Pennsylvania", "PA"), ("Rhode Island", "RI"), ("South Carolina", "SC"),
    ("South Dakota", "SD"), ("Tennessee", "TN"), ("Texas", "TX"), ("Utah", "UT"),
    ("Vermont", "VT"), ("Virginia", "VA"), ("Washington", "WA"), ("West Virginia", "WV"),
    ("Wisconsin", "WI"), ("Wyoming", "WY"), ("District of Columbia", "DC", "D.C."),
    ("Puerto Rico", "PR"), ("Guam", "GU"), ("American Samoa", "AS"),
    ("Northern Mariana Islands", "MP"), ("United States Virgin Islands", "VI", "U.S. Virgin Islands"),
])
REGIONS = {
    "United States": US_STATES,
    "Canada": aliases([
        ("Alberta", "AB"), ("British Columbia", "BC"), ("Manitoba", "MB"),
        ("New Brunswick", "NB"), ("Newfoundland and Labrador", "NL"),
        ("Northwest Territories", "NT"), ("Nova Scotia", "NS"), ("Nunavut", "NU"),
        ("Ontario", "ON"), ("Prince Edward Island", "PE"), ("Quebec", "QC", "Québec"),
        ("Saskatchewan", "SK"), ("Yukon", "YT"),
    ]),
    "Australia": aliases([
        ("Australian Capital Territory", "ACT"), ("New South Wales", "NSW"),
        ("Northern Territory", "NT"), ("Queensland", "QLD"), ("South Australia", "SA"),
        ("Tasmania", "TAS"), ("Victoria", "VIC"), ("Western Australia", "WA"),
    ]),
}


def normalize_geography(value):
    """Expand aliases only in comma-delimited country/state positions.

    Semicolons separate offices. Cities, unknown locations, and missing
    countries remain as supplied; ambiguous bare abbreviations are retained.
    """
    locations = []
    for location in re.split(r"\s*;\s*", str(value or "").strip()):
        parts = [part.strip() for part in location.split(",")]
        country = COUNTRIES.get(parts[-1].casefold())
        # CA/IN/IL can describe states when the country is omitted.
        ambiguous = parts[-1].casefold() in US_STATES and len(parts[-1]) == 2
        if country and (len(parts) >= 3 or not ambiguous):
            parts[-1] = country
            regions = REGIONS.get(country, {})
            if len(parts) >= 3:
                parts[-2] = regions.get(parts[-2].casefold(), parts[-2])
        locations.append(", ".join(parts))
    return "; ".join(locations)
