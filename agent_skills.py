"""Skill templates and tool mappings for Agent."""

import json
import os
import re
from typing import Any
from lang import t
from i18n import K
from skill_loader import SkillLoader


_INSTRUCTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instructions")


def _load_section_instructions() -> dict[str, dict[str, str]]:
    """Load section instructions from instructions/*.json files.

    Each file is named ``{template}.json`` and contains a flat dict of
    phase_name → instruction_text.  Falls back to `_HARDCODED_INSTRUCTIONS_DATA`
    when a template file is missing, so adding a new template never breaks.
    """
    if not os.path.isdir(_INSTRUCTIONS_DIR):
        return dict(_HARDCODED_INSTRUCTIONS_DATA)

    result = {}
    all_templates = set(_HARDCODED_INSTRUCTIONS_DATA.keys())
    for fname in os.listdir(_INSTRUCTIONS_DIR):
        if not fname.endswith(".json"):
            continue
        key = fname[:-5]
        all_templates.add(key)
        fpath = os.path.join(_INSTRUCTIONS_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                result[key] = data
        except (OSError, json.JSONDecodeError):
            pass

    for template in all_templates:
        if template not in result and template in _HARDCODED_INSTRUCTIONS_DATA:
            result[template] = _HARDCODED_INSTRUCTIONS_DATA[template]

    return result


TEMPLATE_TOOLS = {
    "resume": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate"],
    "kodeanalyse": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate", "write_file", "create_issue", "create_refactor_issue"],
    "diffanalyse": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate", "git_diff", "git_log", "create_issue", "create_refactor_issue"],
    "fri": None,
    "one-shot": None,
    "agenten": [
        "list_chunks",
        "read_location",
        "read_chunk",
        "list_files",
        "list_symbols",
        "locate",
        "github_create_pr",
        "github_create_repo", "github_list_repos", "github_create_issue",
        "git_status", "git_add_all", "git_commit", "git_push",
        "git_diff", "git_log",
        "git_create_branch", "git_current_branch", "git_pull", "git_checkout",
        "git_set_remote",
        "git_remote_status"
    ],
    "programmering": ["list_chunks", "read_location", "list_files", "list_symbols", "locate", "edit_file", "write_file", "read_chunk", "add_image", "create_issue", "create_refactor_issue", "run_refinement"],
    "python-arkitektur": ["list_chunks", "read_location", "list_files", "list_symbols", "locate", "write_file", "create_issue", "create_refactor_issue"],
    "billedanalyse": ["add_image", "write_file", "list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate", "create_issue", "create_refactor_issue"],
    "bugfix": ["read_issue", "update_issue_status", "run_tests", "create_refactor_issue", "create_issue", "list_chunks", "read_location", "list_symbols", "locate", "write_file", "edit_file", "list_files"],
     "refactor": ["read_issue", "update_issue_status", "list_chunks", "read_location", "list_files", "list_symbols", "locate", "write_file", "edit_file", "run_tests", "create_issue", "create_refactor_issue", "extract_symbol", "remove_symbol", "add_import", "verify_refactor"],
    "testgenerering": ["list_chunks", "read_location", "list_files", "list_symbols", "locate", "write_file", "edit_file", "run_tests", "create_issue", "create_refactor_issue", "update_issue_status"],
    "issue_handler": [
        "read_issue", "update_issue_status", "run_tests",
        "read_location", "list_chunks", "list_files", "list_symbols", "locate",
        "edit_file", "write_file",
        "create_issue", "create_refactor_issue",
    ],
    "selvforbedring": [
        "read_issue", "update_issue_status", "run_tests",
        "read_location", "list_chunks", "list_files", "list_symbols", "locate",
        "edit_file", "write_file",
        "create_issue", "analyze_own_logs",
        "git_status", "git_diff", "git_commit", "git_push",
        "git_create_branch", "git_checkout",
    ],
    "autoresearch": [
        "read_location", "list_chunks", "list_files", "list_symbols", "locate",
        "edit_file", "write_file", "run_tests",
        "create_issue", "create_refactor_issue",
        "git_status", "git_diff", "git_commit", "git_checkout",
        "read_issue",
    ],
}

# Per-template, per-phase iteration limits (LLM conversation turns).
# Different templates need different budgets: refactor Ekstraher needs 15+
# to create 7 modules, but a simple bugfix Analyse only needs 4.
# Falls back to MAX_TASK_ITERATIONS from config if not specified.
TEMPLATE_PHASE_ITERATION_LIMITS = {
    "kodeanalyse": {
        "Form\u00e5l": 8,
        "Imports og afh\u00e6ngigheder": 8,
        "Arkitektur": 8,
        "Kodekvalitet": 8,
        "Sikkerhed": 8,
    },
    "programmering": {
        "Kravanalyse": 8,
        "Arkitekturdesign": 10,
        "Implementeringsplan": 8,
        "Sikkerhedsanalyse": 8,
        "Uddyb/refinements": 15,
        "Kodeimplementering": 20,
    },
    "refactor": {
        "Analyse": 12,   # plan_phase + list_symbols + read_location(1-2) + analyze_dependencies + write refactor_analyse.md + done
        "Plan": 8,       # Read + write refactor_plan.md (auto-advances)
        "Ekstraher": 15, # extract_symbol does all the work in 1 call per symbol; 34+ symbols in plan
        "Opdat\u00e9r": 16,  # ~5-8 remove_symbol + add_import + verify_refactor
        "Test": 8,       # run_tests + 2-3 fix loops
    },
    "bugfix": {
        "Analyse": 6,
        "Test (Red)": 6,
        "Implementering": 12,
        "Verifikation (Green)": 8,
        "Opdatering": 4,
    },
    "selvforbedring": {
        "Analyser": 6,
        "Diagnostic\u00e9r": 6,
        "Ret": 12,
        "Verific\u00e9r": 8,
        "Commit": 4,
    },
    "testgenerering": {
        "Analyse": 6,
        "Test (Red)": 8,
        "Implementering": 10,
        "Verifikation (Green)": 8,
    },
    "issue_handler": {
        "L\u00e6s": 4,
        "Afklar": 8,
        "Fix": 12,
        "Luk Issue": 4,
    },
    "selvforbedring": {
        "Analyser": 6,
        "Diagnostic\u00e9r": 6,
        "Ret": 15,
        "Verific\u00e9r": 8,
        "Commit": 4,
    },
}

# Per-template, per-phase model overrides. Phases not listed keep the
# current session model. Useful when write-capable models (minimax-m2.5)
# are needed for editing phases but cheaper/faster models suffice for
# read-only analysis phases. Use empty string ``""`` to explicitly keep
# the current execution model without switching.
TEMPLATE_PHASE_MODEL_MAP: dict[str, dict[str, str]] = {
    "selvforbedring": {
        "analyser": "",
        "diagnostic\u00e9r": "",
        "ret": "",
        "verific\u00e9r": "",
        "commit": "",
    },
    "issue_handler": {
        "l\u00e6s": "",
        "analyse": "",
        "afklar": "",
        "fix": "",
        "luk": "",
        "close issue": "",
    },
}


TEMPLATE_TASK_TOOLS = {
    "kodeanalyse": {
        "form\u00e5l": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "imports": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "arkitektur": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "kodekvalitet": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "sikkerhed": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
    },
    "programmering": {
        "kravanalyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "arkitekturdesign": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "implementeringsplan": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "sikkerhedsanalyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "uddyb/refinements": ["read_chunk", "list_files", "list_chunks", "read_location", "write_file", "run_refinement"],
        "kodeimplementering": ["edit_file", "write_file", "run_tests", "locate", "list_symbols", "read_location", "list_chunks", "read_chunk", "list_files", "add_method", "add_function"],
    },
    "agenten": {
        "branch": ["git_current_branch", "git_create_branch", "git_branch_list", "git_checkout", "git_remote_status", "git_pull"],
        "commit": ["git_add_all", "git_commit", "git_status", "git_diff", "git_log"],
        "push": ["git_push", "git_remote_status"],
        "pull request": ["github_create_pr", "git_remote_status", "git_diff", "git_log"],
    },
    "bugfix": {
        "analyse": ["read_issue", "list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "run_tests", "create_issue", "create_refactor_issue", "update_issue_status"],
        "test": ["write_file", "run_tests"],
        "implementering": ["read_location", "locate", "edit_file", "add_method", "add_function", "delete_file", "run_tests"],
        "verifikation": ["run_tests", "edit_file"],
        "opdatering": ["update_issue_status"],
    },
    "refactor": {
        "analyse": ["read_issue", "list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_refactor_issue", "analyze_dependencies", "write_file"],
        "plan": ["read_issue", "update_issue_status", "write_file", "list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "analyze_dependencies", "suggest_module_groups"],
        "ekstraher": ["list_symbols", "read_location", "locate", "write_file", "extract_symbol", "batch_extract_symbols", "verify_refactor", "add_method", "add_function", "list_chunks", "read_chunk"],
        "opdat\u00e9r": ["list_symbols", "read_location", "locate", "edit_file", "update_issue_status", "write_file", "remove_symbol", "add_import", "add_method", "add_function", "delete_file", "verify_refactor"],
        "test": ["run_tests", "edit_file", "update_issue_status", "verify_refactor"],
    },
    "testgenerering": {
        "analyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "run_tests", "create_issue", "create_refactor_issue"],
        "test": ["write_file", "run_tests"],
        "implementering": ["read_location", "locate", "edit_file", "add_method", "add_function", "delete_file", "run_tests"],
        "verifikation": ["run_tests", "edit_file"],
    },
    "issue_handler": {
        "analyse": ["read_issue", "update_issue_status", "run_tests", "read_location", "read_chunk", "list_chunks", "list_files", "locate", "list_symbols", "create_refactor_issue"],
        "fix": ["read_issue", "read_location", "locate", "edit_file", "write_file", "add_method", "add_function", "run_tests"],
        "luk": ["update_issue_status", "read_issue"],
    },
    "selvforbedring": {
        "analyser": ["read_issue", "list_symbols", "locate", "read_location", "list_files", "list_chunks", "run_tests"],
        "diagnostic\u00e9r": ["run_tests", "read_location", "locate", "read_chunk", "list_symbols", "list_files", "list_chunks", "read_issue"],
        "ret": ["edit_file", "locate", "list_symbols", "read_location", "run_tests", "create_issue", "add_method", "add_function", "read_chunk", "list_chunks"],
        "verific\u00e9r": ["run_tests", "update_issue_status"],
        "commit": ["git_create_branch", "git_status", "git_diff", "git_commit", "git_push", "git_checkout", "git_current_branch"],
    },
}

_HARDCODED_INSTRUCTIONS_DATA = {
    "resume": {
        "Overblik": "Skriv afsnittet 'Overblik': beskriv filens form\u00e5l, struktur og hovedindhold.",
        "N\u00f8glepunkter": "Skriv afsnittet 'N\u00f8glepunkter': fremh\u00e6v de vigtigste tekniske detaljer, features og arkitektur.",
        "Konklusion": "Skriv afsnittet 'Konklusion': vurder filens kvalitet, styrker og svagheder.",
        "Anbefalinger": "Skriv afsnittet 'Anbefalinger': foresl\u00e5 konkrete forbedringer og n\u00e6ste skridt.",
    },
    "kodeanalyse": {
        "Form\u00e5l": "L\u00e6s f\u00f8rst docs/formaal.md hvis den findes (read_chunk). SAML eksisterende indhold og dine nye analyseresultater i \u00c9T opdateret dokument. Send FLERE read_location-kald p\u00e5 \u00e9n gang for at gennemg\u00e5 funktionerne hurtigt. Gem derefter med write_file(path='docs/formaal.md', content='<samlet>', overwrite=true). Forklar hvad filen g\u00f8r og dens rolle i projektet. Vurder filens cohesion og om den overholder single responsibility.",
        "Imports og afh\u00e6ngigheder": "L\u00e6s f\u00f8rst docs/imports.md hvis den findes (read_chunk). SAML eksisterende indhold og dine nye analyseresultater i \u00c9T opdateret dokument. Send FLERE read_location-kald p\u00e5 \u00e9n gang for at gennemg\u00e5 funktionernes imports. Gem derefter med write_file(path='docs/imports.md', content='<samlet>', overwrite=true). Gennemg\u00e5 filens imports og eksterne afh\u00e6ngigheder. Bem\u00e6rk ubrugte imports, cirkul\u00e6re afh\u00e6ngigheder og versionsproblemer.",
        "Arkitektur": "L\u00e6s f\u00f8rst docs/arkitektur.md hvis den findes (read_chunk). SAML eksisterende indhold og dine nye analyseresultater i \u00c9T opdateret dokument. Send FLERE read_location-kald p\u00e5 \u00e9n gang for at gennemg\u00e5 strukturen hurtigt. Gem derefter med write_file(path='docs/arkitektur.md', content='<samlet>', overwrite=true). Analys\u00e9r filens struktur, klasser og funktioner. Vurder anvendte design patterns, kobling (coupling) mellem moduler, cohesion, og om SOLID-principperne overholdes.",
        "Kodekvalitet": "L\u00e6s f\u00f8rst docs/kodekvalitet.md hvis den findes (read_chunk). SAML eksisterende indhold og dine nye analyseresultater i \u00c9T opdateret dokument. Send FLERE read_location-kald p\u00e5 \u00e9n gang for at gennemg\u00e5 funktionerne hurtigt. Gem derefter med write_file(path='docs/kodekvalitet.md', content='<samlet>', overwrite=true). Vurder l\u00e6sbarhed, vedligeholdbarhed, test coverage, navngivning (PEP 8), type hints, fejlh\u00e5ndtering, complexity, DRY-princippet og separation of concerns.",
        "Sikkerhed": "L\u00e6s f\u00f8rst docs/sikkerhed.md hvis den findes (read_chunk). SAML eksisterende indhold og dine nye analyseresultater i \u00c9T opdateret dokument. Send FLERE read_location-kald p\u00e5 \u00e9n gang for at gennemg\u00e5 funktionerne hurtigt. Gem derefter med write_file(path='docs/sikkerhed.md', content='<samlet>', overwrite=true). Analys\u00e9r inputvalidering (XSS, SQL injection), autentifikation og session management, access control/autorisation, kryptering og databeskyttelse, fejlh\u00e5ndtering, s\u00e5rbarheder i dependencies, security headers. F\u00f8lg OWASP top 10.",
    },
    "diffanalyse": {
        "Oversigt": "Skriv afsnittet 'Oversigt': beskriv hvad diff'en indeholder af \u00e6ndringer.",
        "Risikovurdering": "Skriv afsnittet 'Risikovurdering': vurder risikoen (h\u00f8j/middel/lav) for hver \u00e6ndret fil.",
        "Brydende \u00e6ndringer": "Skriv afsnittet 'Brydende \u00e6ndringer': identific\u00e9r breaking changes og bagudkompatibilitet.",
        "Kodekvalitet": "Skriv afsnittet 'Kodekvalitet': vurder \u00e6ndringernes kvalitet og konsistens.",
        "Anbefalinger": "Skriv afsnittet 'Anbefalinger': foresl\u00e5 forbedringer til diff'en.",
    },
    "programmering": {
        "Kravanalyse": "Analyser kravene grundigt. Identific\u00e9r funktionelle og ikke-funktionelle krav, input/output, og eventuelle begr\u00e6nsninger. DIN F\u00d8RSTE handling SKAL v\u00e6re write_file \u2014 gem analysen i ./docs/kravanalyse.md. Beskriv hvad systemet skal kunne.",
        "Arkitekturdesign": "Design systemarkitekturen: komponenter, moduler, dataflow og afh\u00e6ngigheder. Overvej relevante design patterns og SOLID-principper. Brug write_file til at gemme designet i ./docs/arkitektur.md. Tegn arkitekturen med tekst.",
        "Implementeringsplan": "L\u00e6s f\u00f8rst tidligere fasedokumenter hvis de findes: brug read_chunk til at indl\u00e6se docs/kravanalyse.md og docs/arkitektur.md for at forst\u00e5 hvad der allerede er besluttet. Planl\u00e6g derefter implementeringen: hvilke filer skal oprettes, i hvilken r\u00e6kkef\u00f8lge, og hvad skal hver fil indeholde. Overvej teststrategi og edge cases. Brug write_file til at gemme planen i ./docs/implementeringsplan.md. Systemet auto-afslutter denne fase n\u00e5r planfilen findes \u2014 du beh\u00f8ver IKKE lave ekstra kald bagefter.",
        "Sikkerhedsanalyse": "Analyser sikkerhedsaspekter (OWASP best practices): inputvalidering (XSS, SQL injection), autentifikation og session management, access control/autorisation, kryptering og databeskyttelse, fejlh\u00e5ndtering og logging, s\u00e5rbarheder i dependencies, security headers (CSP, HSTS, X-Frame-Options), API security (rate limiting, CSRF), mindste rettighedsprincip. Brug write_file til at gemme analysen i ./docs/sikkerhedsanalyse.md.",
        "Uddyb/refinements": "Læs de 4 eksisterende dokumenter i ./docs/ (kravanalyse.md, arkitektur.md, implementeringsplan.md, sikkerhedsanalyse.md). Identificer specifikationer der mangler for at en fungerende standard-løsning kan bygges. Stil spørgsmål ELLER svar selv med reference til standard praksis (Chrome/Firefox/Safari for browsere, PEP 8/SOLID for Python, etc.). Kald run_refinement(workdir='./', rounds=7) som dit FØRSTE tool-kald. LLM'en i refinement-scriptet kører 5+ iterative runder, identificerer mangler, svarer selv, og skriver docs/uddybning_dialog.md. Bagefter: refinér de 4 docs med write_file (path='docs/X.md', overwrite=true) baseret på dialogen. Tilføj nye sektioner som 'Edge cases', 'Konkrete teknologivalg', 'Standard browser-praksis' etc. Fokuser på hvad en udvikler har brug for at vide for at implementere. Stop når alle 4 docs er opdateret OG dialogen er gemt.",
        "Kodeimplementering": "Greenfield-fase: m\u00e5 KUN oprette nye filer. L\u00e6s f\u00f8rst docs/implementeringsplan.md for at se hvilke moduler der skal oprettes. Opret ALLE moduler n\u00e6vnt i planen med write_file \u2014 inkl. undermapper som gui/, engine/, rendering/. Systemet auto-afslutter denne fase n\u00e5r ALLE moduler fra planen findes \u2014 du beh\u00f8ver IKKE lave ekstra kald bagefter. Skriv ren, vedligeholdelsesvenlig kode med korrekt fejlh\u00e5ndtering og logging.",
        "Kodeimplementering (vedligeholdelse)": "Vedligeholdelsesfase: rediger EKSISTERENDE filer med edit_file \u2014 det er dit prim\u00e6re v\u00e6rkt\u00f8j. L\u00e6s f\u00f8rst de eksisterende filer med locate/read_location for at forst\u00e5 strukturen. Brug write_file KUN hvis du skal oprette helt nye moduler der ikke findes i forvejen. Systemet auto-afslutter IKKE denne fase \u2014 du afslutter selv med <<<DONE>>> n\u00e5r implementeringen er f\u00e6rdig. K\u00f8r run_tests for at verificere at dine \u00e6ndringer ikke bryder noget.",
    },
    "python-arkitektur": {
        "Arkitekturplanl\u00e6gning": "Analyser projektet og planl\u00e6g arkitekturen baseret p\u00e5 Python/Flask/HTML/JS best practices. Brug write_file til at oprette ./docs/arkitektur.md med f\u00f8lgende sektioner:\n\n## Systemoversigt\n- Form\u00e5l og m\u00e5ls\u00e6tning\n- Teknologistak (Python, Flask, HTML, JS, database)\n\n## Komponentarkitektur\n- Modulopdeling og ansvar for hvert modul\n- Lagdelt struktur (pr\u00e6sentation, forretningslogik, data)\n- Dataflow mellem komponenter\n\n## Flask-struktur\n- Blueprint-moduler, routes, middleware\n- Request/response-lifecycle\n- Fejlh\u00e5ndtering og logging\n\n## Database design\n- ORM-modeller (SQLAlchemy) og relationer\n- Migration-strategi (Alembic)\n- Indeksering og query-optimering\n\n## Sikkerhed\n- CSRF, XSS, SQL injection beskyttelse\n- Autentifikation og autorisation (Flask-Login, JWT)\n- Milj\u00f8variabler og secrets-h\u00e5ndtering\n\n## Frontend (HTML/JS)\n- Template-struktur (Jinja2) og statiske filer\n- JS-moduler og event-h\u00e5ndtering\n- API-kommunikation (fetch/AJAX)\n\n## Udviklings-workflow\n- Virtuelt milj\u00f8 og afh\u00e6ngighedsstyring\n- Testing (pytest, unittest)\n- Kodekvalitet (flake8, black, mypy, type hints)\n\nF\u00f8lg Python best practices: PEP 8, SOLID, DRY, separation of concerns.",
    },
    "billedanalyse": {
        "Beskrivelse": "Analyser billedet og beskriv hvad der ses. Brug add_image hvis billedet ikke allerede er tilf\u00f8jet. Beskriv motiv, personer, objekter, farver, layout og overordnet indtryk.",
        "Kontekst": "Kontekstualiser billedet. Hvor stammer det fra (app, hjemmeside, dokument)? Hvad er form\u00e5let? Hvilke brugere er det m\u00e5lrettet? Hvilken situation viser det?",
        "Detaljer": "Gennemg\u00e5 specifikke detaljer: tekstindhold, UI-elementer, kodeblokke, tal, datoer, navne, fejlmeddelelser. Fremh\u00e6v alt specifikt og m\u00e5lbart.",
        "Vurdering": "Vurder billedets kvalitet og indhold: Hvad fungerer godt? Hvad kunne forbedres? Er der fejl, inkonsistenser eller mangler? Giv konkrete forbedringsforslag.",
        "Eksport\u00e9r": "Skriv den fulde analyse til en .md fil. Brug write_file til at gemme i ./exports/billedanalyse_{timestamp}.md. Filen skal indeholde alle sektioner samlet. Brug formatet:\n\n# Billedanalyse\n\n## Beskrivelse\n...\n\n## Kontekst\n...\n\n## Detaljer\n...\n\n## Vurdering\n...",
    },
    "bugfix": {
        "Analyse": "TRIN 1: Læs issue med read_issue() — forstå hvad buggen påstår er galt.\nTRIN 2: Find den relevante kode med locate() — brug funktionsnavnet fra issue location.\nTRIN 3: Læs koden med read_location() og sammenlign med buggens påstand.\n  - Er den påståede fejl (f.eks. 'bruger print() i stedet for logging') stadig til stede i koden?\n  - Eller er koden allerede rettet (f.eks. bruger allerede log.warning())?\n  - Hvis koden allerede gør det rigtige: påstanden kan afvises.\nTRIN 4: Hvis påstanden kan afvises (fejlen findes ikke, koden er allerede rettet, eller problemet ikke kan reproduceres), opdater issue-status til 'resolved' med update_issue_status() og skriv en HONEST resolution_note. Afslut med <<<DONE>>>.\nTRIN 5: Hvis fejlen stadig findes, afslut med <<<DONE>>> — resten håndteres i efterfølgende faser.\n\nSKRIV IKKE til filer — kun analyse.",
        "Test (Red)": "Skriv en pytest der fanger bug'en i tests/temp/ (f.eks. tests/temp/test_BUG-095.py). Din FØRSTE handling SKAL være write_file for at oprette testfilen i tests/temp/. Kør testen med run_tests(test_path='tests/temp/...') — den SKAL fejle (rød fase). Hvis testen består (i stedet for at fejle), er bug'en allerede fikset — opdater issue-status til 'resolved' og afslut med <<<DONE>>>.",
        "Implementering": "Ret kildekoden med den mindst mulige ændring. Brug locate/read_location til at læse relevant kode og forstå strukturen. Brug edit_file med old_text/new_text til at redigere eksisterende funktioner. Brug add_method(class_name='...', method_code='def ...') til at tilføje nye metoder til en klasse. Brug add_function(function_code='def ...') til at tilføje module-level funktioner. Brug IKKE write_file — filen findes allerede. Hvis fejlen allerede er rettet (testen bestod i rød fase), redigér IKKE filer — opdater issue-status til 'resolved' med update_issue_status() og afslut straks med <<<DONE>>>.",
        "Verifikation (Green)": "Kør temp-testen med run_tests(test_path='tests/temp/...') — den SKAL bestå (grøn fase). Kør HELE testsuiten med run_tests() for at verificere ingen regressions (tests/temp/ er automatisk ekskluderet).",
        "Opdatering": "Opdater issue-status til 'resolved' med update_issue_status(). Tilføj en kort resolution_note om hvad der blev fikset.",
    },
    "refactor": {
        "Analyse": "⚠️ BUDGET: MAX 6 tool-kald i Analyse. Din plan må kun indeholde: (1) list_symbols, (2-3) read_location MAX 2, (4) write_file(refactor_analyse.md), (5) done. Brug IKKE list_files, create_todo, analyze_dependencies, eller update_todo. Notér SOLID-analyse og modul-kortlægning direkte i write_file-indholdet — ikke som separate trin.\n\nTRIN 1: Kald list_symbols(filepath='{source_file}').\nTRIN 2: Læs MAX 2 funktioner/klasser med read_location() — brug PRÆCISE navne fra list_symbols.\nTRIN 3: Skriv alt (kortlægning, dependensnoter, SOLID) i write_file(path='refactor_analyse.md', content='...'). Gem i RODEN (ikke docs/).",
        "Plan": "Læs `refactor_analyse.md` med read_chunk() for at få analysen — du behøver IKKE læse symboler/funktioner igen. Brug write_file(path='refactor_plan.md', content='...') til at skrive planen i RODEN (ikke docs/). Systemet auto-afslutter denne fase så snart refactor_plan.md er skrevet.\n📋 Start med at kalde **plan_phase**. <<<DONE>>> først når planen er skrevet.",
        "Ekstraher": "Opret NYE .py modulfiler med batch_extract_symbols() i.h.t. planen fra forrige fase. Følg refactor_plan.md TIL PUNKT OG PRIKKE — ALLE moduler nævnt i planen SKAL oprettes. 🔥 DETERMINISTISKE VÆRKTØJER: Brug batch_extract_symbols(source='{source_file}', symbols='_RateLimiter, _is_development_mode, _rate_limit', target='middleware.py') for at flytte FLERE symboler i ét kald — systemet kopierer alle symboler inkl. imports, fjerner dem fra {source_file}, OG tilføjer imports i {source_file}. Brug extract_symbol kun hvis batch_extract_symbols fejler, eller til enkelt-symboler. Brug verify_refactor(source='{source_file}') bagefter for at bekræfte syntaks. Du skal oprette FILER som routes.py, session_manager.py, file_handler.py osv. — IKKE write_file til refactor_plan.md (den findes allerede fra Plan-fasen og er auto-indlæst i din kontekst ovenfor).\n📋 Din opgaveplan (via **plan_phase**) skal indeholde konkrete tool-kald med parametre, f.eks.:\n  `batch_extract_symbols(source='refac_test.py', symbols=['DATABASE_URL', 'MAX_RETRIES', ...], target='config.py')`\n  `verify_refactor(source='refac_test.py')`\nAngiv symbolnavnene eksplicit i hvert trin så rækkefølgen og indholdet er tydeligt.\n🔥 EFFEKTIVITETSGUIDE: Udfør trinene i din plan i rækkefølge. Du har KUN 15 iterations. Fuldfør ét modul HELT før du går til det næste: batch_extract_symbols for ALLE symbolgrupper i modulet → verify_refactor → næste modul. Systemet auto-afslutter denne fase så snart ALLE moduler nævnt i planen er oprettet OG hvert modul indeholder sine planlagte symboler. 📋 Brug **update_todo** for at markere hvert modul færdigt. Symbolerne er allerede opdelt i komplette chunks (max 15 symboler / ~500 tegn) — et chunk pr. batch_extract_symbols kald. Kald ALLE chunks for ét modul før du verificerer.",
        "Opdatér": "Opdater {source_file} med remove_symbol() og add_import() — DETERMINISTISKE værktøjer der ikke kræver LLM. Brug remove_symbol(source='{source_file}', symbol_name='FunktionsNavn') for at fjerne et symbol der allerede er flyttet til et modul. Brug add_import(source='{source_file}', module='routes', symbol='FunktionsNavn') for at tilføje en import til et modul. Brug verify_refactor(source='{source_file}') til at tjekke syntaks.\n📋 Din opgaveplan (via **plan_phase**) bør angive remove_symbol → add_import → verify_refactor for hvert modul. Du har KUN 12 iterations. Brug edit_file KUN hvis remove_symbol/add_import ikke kan klare opgaven. <<<DONE>>> først når {source_file} er syntaktisk gyldig og alle relevante imports er tilføjet.",
        "Test": "Kør testsuiten med run_tests(). Hvis tests fejler, ret import-stier med edit_file() og genkør. Bliv ved indtil ALLE tests består. Opdater issue-status til 'resolved' med update_issue_status() når tests består. 📋 Brug **update_todo** for at markere fremdrift.",
    },
    "testgenerering": {
        "Analyse": "Læs filen med read_chunk(). Forstå alle klasser, funktioner, metoder og imports. Identificér hvilke der allerede har tests og hvilke der mangler. Opret et issue med create_issue() hvis du finder kode der mangler tests.",
        "Test (Red)": "Skriv pytest-tests for den manglende dækning i tests/temp/. Opret en NY testfil med write_file (testfilen må ikke findes i forvejen). Kør testen med run_tests(test_path='tests/temp/...') — den SKAL bestå (grøn fase). Hvis testen fejler, ret koden med edit_file og genkør.",
        "Implementering": "Hvis produktionskoden skal ændres for at gøres testbar, brug edit_file til målrettede ændringer. Brug IKKE write_file — produktionsfilen findes allerede.",
        "Verifikation (Green)": "Kør HELE testsuiten med run_tests() for at verificere ingen regressions. Opdater issue-status til 'resolved' med update_issue_status() hvis et TST-issue blev løst.",
    },
    "issue_handler": {
        "Læs": "Læs den tildelte issue med read_issue(). Forstå beskrivelse, location, impact og proposed_fix. Du må IKKE læse kildekode eller redigere filer i dette trin. Brug KUN read_issue(). Afslut med <<<DONE>>>.",
        "Afklar": "TRIN 1: Læs issue med read_issue() — forstå hvad buggen påstår er galt (location, proposed_fix, description).\nTRIN 2: Find den relevante kode med locate() — brug funktionsnavnet fra issue location.\nTRIN 3: Læs koden med read_chunk() og sammenlign med buggens påstand.\n  - Er den påståede fejl stadig til stede i koden?\n  - Eller er koden allerede rettet (f.eks. bruger logging i stedet for print())?\n  - Hvis koden allerede gør det rigtige: påstanden kan afvises — buggen er allerede løst.\nTRIN 4: Hvis fejlen ALLEREDE er løst: opdater status til 'resolved' med update_issue_status() og skriv en HONEST resolution_note — sig 'Allerede løst — ingen ændringer foretaget' frem for at påstå noget blev implementeret. Afslut med <<<DONE>>>.\nTRIN 5: Hvis fejlen stadig findes: afslut med <<<DONE>>> og fortsæt til Fix.\n\nAnalyser om der mangler detaljer (præcis location, acceptance criteria, repro steps) — opdater i så fald med update_issue_status() (men må IKKE sætte status='resolved').",
        "Fix": "Tjek issue.type med read_issue(). Tjek om issuet allerede har status 'resolved' — hvis ja, redigér INGEN filer og afslut straks med <<<DONE>>>.\n\n"
             "**edit_file BRUGSANVISNING (LÆS DETTE FØRST):**\n"
             "- AST-tilstand (BEDST til .py): Læs funktionen med locate(name='funktionsnavn') FØRST. "
             "Kald derefter edit_file(path='fil.py', symbol='funktionsnavn', new_text='hele den nye funktion'). "
             "Systemet udskifter automatisk den gamle funktion med din nye tekst — old_text ignoreres.\n"
             "- Search-and-replace (faldback): old_text skal være en PRÆCIS byte-kopi fra filen. "
             "Brug locate() for at se den nøjagtige tekst. Redigér ALDRIG old_text — den skal kopieres direkte.\n"
             "- For at tilføje i slutningen af en fil: locate(name='<sidste_funktion>'), "
             "brug edit_file(symbol='<sidste_funktion>', new_text='...gamle funktion...\\n\\n...ny funktion...').\n"
             "- For NYE filer: brug write_file() — aldrig edit_file.\n\n"
             "**Hvis type = architecture:** Følg refactor-workflowet:\n"
             "  1. Analyse — Læs kildekoden med read_chunk(), forstå struktur og afhængigheder\n"
             "  2. Plan — Beslut hvordan koden opdeles/omstruktureres\n"
             "  3. Ekstraher — Opret nye filer med write_file()\n"
             "  4. Opdatér — Redigér den originale fil med edit_file() (fjern flyttet kode, tilføj imports)\n"
             "  5. Test — Kør run_tests(), ret indtil alle består\n\n"
             "**Hvis type = feature:** Implementér den nye feature:\n"
             "  1. Læs projektets eksisterende kode med list_symbols() + locate() for at forstå strukturen\n"
             "  2. Beslut hvad der skal tilføjes (nye filer, nye funktioner, ændringer i eksisterende kode)\n"
             "  3. Implementér — Brug write_file() til nye filer. Brug edit_file(symbol='funktionsnavn') til at redigere eksisterende funktioner. "
             "For at tilføje en ny funktion sidst i en fil: edit_file(symbol='<sidste_funktion>', new_text='<sidste_funktion>...\\n\\n<ny_funktion>...')\n"
             "  4. Test — Kør run_tests(), ret indtil alle består\n\n"
             "**Hvis type = bug:** Ret fejlen med edit_file() i AST-tilstand: locate(name='funktionsnavn') → edit_file(path='fil.py', symbol='funktionsnavn', new_text='...'). "
             "Brug ALDRIG search-and-replace til Python-filer medmindre du har set den PRÆCISE tekst med locate().\n\n"
             "Du må IKKE selv markere issue som resolved — det gøres i Luk Issue. Afslut med <<<DONE>>>.",
        "Luk Issue": "DIN FØRSTE OG ENESTE HANDLING: Kald update_issue_status(issue_id='...', status='resolved', resolution_note='...'). Find issue_id med read_issue() hvis du ikke kender den. Tilføj en PRÆCIS resolution_note: beskriv HVAD der blev ændret, HVORFOR, og bekræft at ALLE tests består. Afslut med <<<DONE>>> efter update_issue_status er kaldt.",
        "read": "Read the assigned issue with read_issue(). Understand description, location, impact and proposed_fix. Do NOT read source code or edit files in this step. Use ONLY read_issue(). End with <<<DONE>>>.",
        "læs": "Læs den tildelte issue med read_issue(). Forstå beskrivelse, location, impact og proposed_fix. Du må IKKE læse kildekode eller redigere filer i dette trin. Brug KUN read_issue(). Afslut med <<<DONE>>>.",
        "afklar": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "clarify": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "fix": "Check issue.type with read_issue(). If already resolved, do NOT edit any files — end with <<<DONE>>>.\n\n"
             "**edit_file USAGE:**\n"
             "- AST mode (BEST for .py): locate(name='function_name') FIRST to see exact code. "
             "Then edit_file(path='file.py', symbol='function_name', new_text='entire new function'). old_text is ignored.\n"
             "- Search-and-replace (fallback): old_text MUST be an exact byte copy from the file. Use locate() to see the exact text. NEVER rewrite or reformat old_text.\n"
             "- Append at end of file: locate(name='<last_function>') → "
             "edit_file(symbol='<last_function>', new_text='...old function...\\n\\n...new function...').\n"
             "- NEW files: use write_file() — never edit_file().\n\n"
             "**Feature type:** Understand structure with locate() → write_file() for new files → edit_file(symbol=) to modify existing functions → Test\n"
             "**Bug type:** locate(name) → edit_file(path, symbol=name, new_text='...'). Run run_tests().\n"
             "**Architecture type:** Follow refactor workflow.\n\n"
             "Do NOT mark issue as resolved yourself — done in Close Issue. End with <<<DONE>>>.",
        "luk": "YOUR FIRST AND ONLY ACTION: Call update_issue_status(issue_id='...', status='resolved', resolution_note='...'). Use read_issue() to find the issue_id if needed. Add a PRECISE resolution_note: describe WHAT was changed, WHY, and confirm ALL tests pass. End with <<<DONE>>> after calling update_issue_status.",
        "close issue": "YOUR FIRST AND ONLY ACTION: Call update_issue_status(issue_id='...', status='resolved', resolution_note='...'). Use read_issue() to find the issue_id if needed. Add a PRECISE resolution_note: describe WHAT was changed, WHY, and confirm ALL tests pass. End with <<<DONE>>> after calling update_issue_status.",
        "en_fix": "Check issue.type with read_issue(). If the issue already has status 'resolved', do NOT edit any files and end immediately with <<<DONE>>>. Fix the bug with edit_file() — use EXACT text from the file. Run run_tests(). Do NOT mark issue as resolved yourself — that is done in Close Issue. End with <<<DONE>>>.",
        "da_fix": "Tjek issue.type med read_issue(). Hvis issuet har status 'resolved', redigér INGEN filer og afslut straks med <<<DONE>>>.\n\n"
             "**edit_file BRUGSANVISNING:**\n"
             "- AST-tilstand (BEDST): Læs funktionen med locate(name='funktionsnavn') FØRST. "
             "Kald edit_file(path='fil.py', symbol='funktionsnavn', new_text='hele den nye funktion'). old_text ignoreres.\n"
             "- Search-and-replace (KUN når AST ikke virker): old_text skal være PRÆCIS kopi fra filen. Redigér ALDRIG old_text.\n"
             "- Tilføj sidst i fil: locate(name='<sidste_funktion>') → edit_file(symbol='<sidste_funktion>', new_text='...gammel...\\n\\n...ny...').\n"
             "- NYE filer: brug write_file().\n\n"
             "**Hvis type = architecture:** Refactor workflow: Analyse → Plan → Ekstraher (write_file) → Opdatér (edit_file AST) → Test\n"
             "**Hvis type = feature:** locate() for at forstå struktur → write_file() til nye filer → edit_file(symbol=) til eksisterende funktioner → Test\n"
             "**Hvis type = bug:** locate(navn) → edit_file(path, symbol=navn, new_text='...'). Kør run_tests().\n\n"
             "Du må IKKE selv markere issue som resolved — det gøres i Luk Issue. Afslut med <<<DONE>>>.",
        "en_clarify": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "da_afklar": "Læs issue med read_issue(). Analyser om der mangler detaljer (præcis location, acceptance criteria, repro steps) — opdater i så fald med update_issue_status(). Læs den relevante kildekode med read_chunk() og kør run_tests() for at verificere om fejlen stadig eksisterer. Hvis fejlen ALLEREDE er løst: opdater status til 'resolved' med update_issue_status() og skriv en HONEST resolution_note. Afslut med <<<DONE>>>.",
    },
    "selvforbedring": {
        "Analyser": "TRIN 1: L\u00e6s CORE-issue med read_issue() \u2014 forst\u00e5 hvilken funktion/omr\u00e5de har h\u00f8jest fejlrate.\nTRIN 2: Brug list_symbols() p\u00e5 den angivne source-fil for at se ALLE symboler.\nTRIN 3: L\u00e6s den fejlende kode med read_location() eller locate() \u2014 forst\u00e5 hvad koden g\u00f8r og hvorfor den fejler.\nTRIN 4: Afslut med <<<DONE>>> n\u00e5r du har l\u00e6st nok til at forst\u00e5 problemet.\n\nBrug KUN l\u00e6sev\u00e6rkt\u00f8jer \u2014 redig\u00e9r INGENTING.",
        "Diagnostic\u00e9r": "TRIN 1: K\u00f8r run_tests() for at se hvilke tests der fejler \u2014 bekr\u00e6ft fejlm\u00f8nsteret.\nTRIN 2: L\u00e6s koden grundigt med read_location() \u2014 identific\u00e9r den pr\u00e6cise rod\u00e5rsag.\nTRIN 3: Afslut med <<<DONE>>> n\u00e5r du forst\u00e5r HVAD der skal rettes og HVORDAN.\n\nBrug KUN l\u00e6sev\u00e6rkt\u00f8jer \u2014 redig\u00e9r INGENTING.",
        "Ret": "RET-FASEN: redig\u00e9r koden med edit_file \u2014 det er dit prim\u00e6re v\u00e6rkt\u00f8j.\n\n"
              "1. Brug locate(name='funktionsnavn') eller locate(name='VARIABELNAVN') for at se koden.\n"
              "2. Kopi\u00e9r den PR\u00c6CISE tekst du vil \u00e6ndre fra l\u00e6seresultatet. "
              "Brug ALDRIG tekst fra hukommelsen \u2014 kun PR\u00c6CIST fra locate/read_location.\n"
              "3. Kald edit_file(path='fil.py', old_text='...', new_text='...') med den kopierede tekst.\n"
              "4. K\u00f8r run_tests() \u2014 ALLE tests SKAL best\u00e5.\n"
              "5. Hvis tests fejler: ret med edit_file og genk\u00f8r.\n"
              "6. N\u00e5r alle tests best\u00e5r: afslut med <<<DONE>>>.\n\n"
              "\u26a0\ufe0f ALDRIG brug symbol= for variable \u2014 symbol= virker KUN for funktioner og klasser. "
              "For variable som SECTION_INSTRUCTIONS skal du ALTID bruge old_text/new_text.",
        "Verific\u00e9r": "K\u00f8r HELE testsuiten med run_tests(). ALLE tests SKAL best\u00e5. Hvis tests fejler, g\u00e5 tilbage til Ret-fasen og ret med edit_file. N\u00e5r alle tests best\u00e5r, opdater issue-status til 'resolved' med update_issue_status() og skriv pr\u00e6cis resolution_note.",
        "Commit": "TRIN 1: Brug git_create_branch('self-improve/CORE-xxx') for at oprette en branch.\nTRIN 2: Brug git_status() og git_diff() for at se hvad der er \u00e6ndret.\nTRIN 3: Brug git_commit() med en beskrivende commit-message.\nTRIN 4: Brug git_push() for at skubbe til remote.\nAfslut med <<<DONE>>>.",
    },

    "agenten": {
        "Branch": "TRIN 1: Kald git_current_branch() for at se hvilken branch du er p\u00e5.\\nTRIN 2: Brug git_create_branch('feature/xxx') for at oprette en ny branch.\\nTRIN 3: Bekr\u00e6ft med git_current_branch().\\nAfslut med <<<DONE>>>.",
        "Commit": "TRIN 1: Brug git_status() og git_diff() for at se \u00e6ndringer.\\nTRIN 2: Stage med git_add_all().\\nTRIN 3: Commit med git_commit('besked').\\nAfslut med <<<DONE>>>.",
        "Push": "TRIN 1: Brug git_push() for at skubbe til remote.\\nTRIN 2: Bekr\u00e6ft med git_remote_status().\\nAfslut med <<<DONE>>>.",
        "Pull request": "TRIN 1: Brug github_create_pr() med title og body.\\nTRIN 2: Bekr\u00e6ft at PR'en er oprettet.\\nAfslut med <<<DONE>>>.",
    },
}
SECTION_INSTRUCTIONS = _load_section_instructions()


def refresh_skills(agent: Any) -> None:
    """refresh skills.
    
    Args:
        agent: The agent whose skills need to be refreshed
    """
    try:
        agent._skills = SkillLoader.load_all(lang=agent.lang)
    except Exception as e:
        # Log the error (assuming there's a logging mechanism available)
        print(f"Error refreshing skills: {e}")
        return None


def match_skills(agent: Any, prompt: str) -> list[dict[str, Any]]:
    """match skills.
    
    Args:
        agent:
        prompt:"""
    scored = SkillLoader.find_all_for_task(prompt, agent._skills, top=3)
    agent._active_skills = [s for s in scored if s.get("base") or has_matching_intent(agent, s)]
    template_match = [s for s in agent._skills if s.get("template") and s["template"] == agent.active_template and s not in agent._active_skills]
    agent._active_skills.extend(template_match)
    if agent._active_skills:
        names = [f"{s['name']}[{'BASE' if s.get('base') else 'MATCH'}]" for s in agent._active_skills]
        agent._log("SKILL", f"Aktive skills ({len(agent._active_skills)})", ", ".join(names))
    else:
        agent._log("SKILL", "Ingen skills matchede", "")
    return agent._active_skills


def has_matching_intent(agent: Any, skill: dict[str, Any]) -> bool:
    """has matching intent.
    
    Args:
        agent:
        skill:"""
    intent = skill.get("template") or skill.get("intent")
    return not agent.active_template or intent == agent.active_template or skill.get("base")


def format_skills_for_prompt(agent: Any) -> str:
    """format skills for prompt.
    
    Args:
        agent:"""
    if not agent._active_skills:
        return ""
    lines = ["\n## \U0001f4cb Retningslinjer (ikke v\u00e6rkt\u00f8jer)\n"]
    for s in agent._active_skills:
        tag = "BASE" if s.get("base") else "MATCH"
        lines.append(f"- **{s.get('name', 'unknown')}** [{tag}]: {s.get('description', '')[:120]}")
        body = s.get("body", "")
        if body and not s.get("base"):
            lines.append("")
            lines.append(body)
    return "\n".join(lines)


def get_templates(agent: Any) -> dict[str, dict[str, str | None]]:
    """get templates.
    
    Args:
        agent:"""
    lang_instr = t(K.ANSWER_IN, agent.lang)
    criteria_instr = t(K.CRITERIA_DECOMPOSE, agent.lang)
    return {
        "resume": {
            "name": t(K.T_RESUME, agent.lang),
            "prompt": t(K.TP_RESUME, agent.lang).replace("{lang_instruction}", lang_instr),
            "fallback": t(K.TF_RESUME, agent.lang),
        },
        "kodeanalyse": {
            "name": t(K.T_KODEANALYSE, agent.lang),
            "prompt": t(K.TP_KODEANALYSE, agent.lang).replace("{lang_instruction}", lang_instr),
            "fallback": t(K.TF_KODEANALYSE, agent.lang),
        },
        "diffanalyse": {
            "name": t(K.T_DIFFANALYSE, agent.lang),
            "prompt": t(K.TP_DIFFANALYSE, agent.lang).replace("{lang_instruction}", lang_instr),
            "fallback": t(K.TF_DIFFANALYSE, agent.lang),
        },
        "fri": {
            "name": t(K.T_FRI, agent.lang),
            "prompt": t(K.TP_FRI, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": None
        },
        "one-shot": {
            "name": t(K.T_ONE_SHOT, agent.lang),
            "prompt": t(K.TP_ONE_SHOT, agent.lang).replace("{lang_instruction}", lang_instr),
            "fallback": None
        },
        "agenten": {
            "name": t(K.T_AGENTEN, agent.lang),
            "prompt": t(K.TP_AGENTEN, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_AGENTEN, agent.lang),
        },
        "programmering": {
            "name": t(K.T_PROGRAMMERING, agent.lang),
            "prompt": t(K.TP_PROGRAMMERING, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_PROGRAMMERING, agent.lang),
        },
        "python-arkitektur": {
            "name": t(K.T_PYTHON_ARKITEKTUR, agent.lang),
            "prompt": t(K.TP_PYTHON_ARKITEKTUR, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_PYTHON_ARKITEKTUR, agent.lang),
        },
        "billedanalyse": {
            "name": t(K.T_BILLEDANALYSE, agent.lang),
            "prompt": t(K.TP_BILLEDANALYSE, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_BILLEDANALYSE, agent.lang),
        },
        "bugfix": {
            "name": t(K.T_BUGFIX, agent.lang),
            "prompt": t(K.TP_BUGFIX, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_BUGFIX, agent.lang),
        },
        "refactor": {
            "name": t(K.T_REFACTOR, agent.lang),
            "prompt": t(K.TP_REFACTOR, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_REFACTOR, agent.lang),
        },
        "testgenerering": {
            "name": t(K.T_TESTGENERERING, agent.lang),
            "prompt": t(K.TP_TESTGENERERING, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_TESTGENERERING, agent.lang),
        },
        "issue_handler": {
            "name": t(K.T_ISSUE_HANDLER, agent.lang),
            "prompt": t(K.TP_ISSUE_HANDLER, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_ISSUE_HANDLER, agent.lang),
        },
        "selvforbedring": {
            "name": t(K.T_SELVFORBEDRING, agent.lang),
            "prompt": t(K.TP_SELVFORBEDRING, agent.lang).replace("{lang_instruction}", lang_instr).replace("{criteria_instr}", criteria_instr),
            "fallback": t(K.TF_SELVFORBEDRING, agent.lang),
        },
    }
