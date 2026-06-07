"""Skill templates and tool mappings for Agent."""

import re
from typing import Any
from lang import t
from i18n import K
from skill_loader import SkillLoader


TEMPLATE_TOOLS = {
    "resume": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate"],
    "kodeanalyse": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate", "create_issue", "create_refactor_issue"],
    "diffanalyse": ["list_chunks", "read_location", "read_chunk", "list_files", "list_symbols", "locate", "git_diff", "git_log", "create_issue", "create_refactor_issue"],
    "fri": None,
    "agenten": [
        "list_chunks",
        "read_location",
        "read_chunk",
        "list_files",
        "list_symbols",
        "locate",
        "github_create_pr",
        "git_status", "git_add_all", "git_commit", "git_push",
        "git_diff", "git_log",
        "git_create_branch", "git_current_branch", "git_pull", "git_checkout",
        "git_remote_status"
    ],
    "programmering": ["list_chunks", "read_location", "list_files", "list_symbols", "locate", "write_file", "add_image", "create_issue", "create_refactor_issue"],
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
}

# Per-template, per-phase iteration limits (LLM conversation turns).
# Different templates need different budgets: refactor Ekstraher needs 15+
# to create 7 modules, but a simple bugfix Analyse only needs 4.
# Falls back to MAX_TASK_ITERATIONS from config if not specified.
TEMPLATE_PHASE_ITERATION_LIMITS = {
    "kodeanalyse": {
        "Form\u00e5l": 6,
        "Imports og afh\u00e6ngigheder": 6,
        "Arkitektur": 8,
        "Kodekvalitet": 6,
        "Sikkerhed": 6,
    },
    "programmering": {
        "Kravanalyse": 8,
        "Arkitekturdesign": 10,
        "Implementeringsplan": 8,
        "Sikkerhedsanalyse": 8,
        "Kodeimplementering": 20,
    },
    "refactor": {
        "Analyse": 4,    # Read issue + list_symbols + a few read_location
        "Plan": 4,       # Read + write refactor_plan.md (auto-advances)
        "Ekstraher": 15, # extract_symbol does all the work in 1 call per symbol; 34+ symbols in plan
        "Opdat\u00e9r": 12,  # ~5-8 edit_file + read_location for imports
        "Test": 8,       # run_tests + 2-3 fix loops
    },
    "bugfix": {
        "Analyse": 6,
        "Test (Red)": 6,
        "Implementering": 12,
        "Verifikation (Green)": 8,
        "Opdatering": 4,
    },
}


TEMPLATE_TASK_TOOLS = {
    "kodeanalyse": {
        "form\u00e5l": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_issue", "create_refactor_issue"],
        "imports": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_issue", "create_refactor_issue"],
        "arkitektur": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_issue", "create_refactor_issue"],
        "kodekvalitet": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_issue", "create_refactor_issue"],
        "sikkerhed": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_issue", "create_refactor_issue"],
    },
    "programmering": {
        "kravanalyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "arkitekturdesign": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "implementeringsplan": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "sikkerhedsanalyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "write_file", "create_issue", "create_refactor_issue"],
        "kodeimplementering": ["write_file", "edit_file", "run_tests", "locate", "list_symbols", "read_location", "list_chunks", "read_chunk", "list_files"],
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
        "implementering": ["read_location", "locate", "edit_file", "run_tests"],
        "verifikation": ["run_tests", "edit_file"],
        "opdatering": ["update_issue_status"],
    },
    "refactor": {
        "analyse": ["read_issue", "list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "create_refactor_issue", "analyze_dependencies"],
        "plan": ["read_issue", "update_issue_status", "write_file", "list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "analyze_dependencies", "suggest_module_groups"],
        "ekstraher": ["list_symbols", "read_location", "locate", "write_file", "extract_symbol", "verify_refactor", "list_chunks", "read_chunk"],
        "opdat\u00e9r": ["list_symbols", "read_location", "locate", "edit_file", "update_issue_status", "write_file", "remove_symbol", "add_import", "verify_refactor"],
        "test": ["run_tests", "edit_file", "update_issue_status", "verify_refactor"],
    },
    "testgenerering": {
        "analyse": ["list_files", "list_chunks", "read_location", "read_chunk", "locate", "list_symbols", "run_tests", "create_issue", "create_refactor_issue"],
        "test": ["write_file", "run_tests"],
        "implementering": ["read_location", "locate", "edit_file", "run_tests"],
        "verifikation": ["run_tests", "edit_file"],
    },
    "issue_handler": {
        "analyse": ["read_issue", "update_issue_status", "run_tests", "read_location", "read_chunk", "list_chunks", "list_files", "locate", "list_symbols", "create_refactor_issue"],
        "fix": ["read_location", "locate", "edit_file", "write_file", "run_tests"],
        "luk": ["update_issue_status"],
    },
}

SECTION_INSTRUCTIONS = {
    "resume": {
        "Overblik": "Skriv afsnittet 'Overblik': beskriv filens form\u00e5l, struktur og hovedindhold.",
        "N\u00f8glepunkter": "Skriv afsnittet 'N\u00f8glepunkter': fremh\u00e6v de vigtigste tekniske detaljer, features og arkitektur.",
        "Konklusion": "Skriv afsnittet 'Konklusion': vurder filens kvalitet, styrker og svagheder.",
        "Anbefalinger": "Skriv afsnittet 'Anbefalinger': foresl\u00e5 konkrete forbedringer og n\u00e6ste skridt.",
    },
    "kodeanalyse": {
        "Form\u00e5l": "Skriv afsnittet 'Form\u00e5l': forklar hvad filen g\u00f8r og dens rolle i projektet.",
        "Imports og afh\u00e6ngigheder": "Skriv afsnittet 'Imports og afh\u00e6ngigheder': gennemg\u00e5 filens imports og eksterne afh\u00e6ngigheder.",
        "Arkitektur": "Skriv afsnittet 'Arkitektur': analys\u00e9r filens struktur, klasser og funktioner.",
        "Kodekvalitet": "Skriv afsnittet 'Kodekvalitet': vurder kodens l\u00e6sbarhed, vedligeholdbarhed og test coverage.",
        "Sikkerhed": "Skriv afsnittet 'Sikkerhed': identific\u00e9r potentielle sikkerhedsproblemer og s\u00e5rbarheder.",
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
        "Sikkerhedsanalyse": "Analyser sikkerhedsaspekter: inputvalidering, autentifikation, kryptering, h\u00e5ndtering af f\u00f8lsomme data (passwords, keys). F\u00f8lg OWASP best practices og princip om mindste rettighed. Brug write_file til at gemme analysen i ./docs/sikkerhedsanalyse.md.",
        "Kodeimplementering": "Greenfield-fase: m\u00e5 KUN oprette nye filer. Hvis der allerede findes .py-filer i workdir (p\u00e5 n\u00e6r framework-filer), skal du straks afslutte med fejl \u2014 projektet h\u00f8rer til i en bugfix- eller refactor-skabelon. Implement\u00e9r koden baseret p\u00e5 arkitekturdesign og implementeringsplan. Brug write_file til at oprette hver ny fil. Skriv ren, vedligeholdelsesvenlig kode med korrekt fejlh\u00e5ndtering og logging.",
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
        "Analyse": "TRIN 1: Læs issue med read_issue() — forstå hvad buggen påstår er galt.\nTRIN 2: Find den relevante kode med locate() — brug funktionsnavnet fra issue location.\nTRIN 3: Læs koden med read_chunk() og sammenlign med buggens påstand.\n  - Er den påståede fejl (f.eks. 'bruger print() i stedet for logging') stadig til stede i koden?\n  - Eller er koden allerede rettet (f.eks. bruger allerede log.warning())?\n  - Hvis koden allerede gør det rigtige: påstanden kan afvises.\nTRIN 4: Hvis påstanden kan afvises (fejlen findes ikke, koden er allerede rettet, eller problemet ikke kan reproduceres), opdater issue-status til 'resolved' med update_issue_status() og skriv en HONEST resolution_note. Afslut med <<<DONE>>>.\nTRIN 5: Hvis fejlen stadig findes, afslut med <<<DONE>>> — resten håndteres i efterfølgende faser.\n\nSKRIV IKKE til filer — kun analyse.",
"Test (Red)": "Skriv en pytest der fanger bug'en i tests/temp/ (f.eks. tests/temp/test_BUG-095.py). Din FØRSTE handling SKAL være write_file for at oprette testfilen i tests/temp/. Læs INGENTING først — du har allerede læst nok i Analyse-fasen. Kør testen med run_tests(test_path='tests/temp/...') — den SKAL fejle (rød fase). Hvis testen består (i stedet for at fejle), er bug'en allerede fikset — opdater issue-status til 'resolved' og afslut med <<<DONE>>>.",
"Implementering": "Ret kildekoden med den mindst mulige ændring. Brug read_chunk til at læse filen og kopiér tekst direkte ind i edit_file's old_text — brug PRÆCIS de tegn filen indeholder. Brug IKKE write_file — filen findes allerede. Hvis fejlen allerede er rettet (testen bestod i rød fase), redigér IKKE filer — opdater issue-status til 'resolved' med update_issue_status() og afslut straks med <<<DONE>>>.",
"Verifikation (Green)": "Kør temp-testen med run_tests(test_path='tests/temp/...') — den SKAL bestå (grøn fase). Kør HELE testsuiten med run_tests() for at verificere ingen regressions (tests/temp/ er automatisk ekskluderet).",
        "Opdatering": "Opdater issue-status til 'resolved' med update_issue_status(). Tilføj en kort resolution_note om hvad der blev fikset.",
    },
    "refactor": {
        "Analyse": "TRIN 1: Brug list_symbols(filepath='api_server.py') for at se ALLE symboler.\nTRIN 2: Brug read_location() p\u00e5 MINDST 5 funktioner/klasser for at forst\u00e5 struktur, dekoratorer og afh\u00e6ngigheder. Identific\u00e9r hvilke funktioner der har @app.route, @app.before_request osv.\nTRIN 3: Kortl\u00e6g ansvarsomr\u00e5der (routes, sikkerhed, sessioner, filh\u00e5ndtering, billeder, modeller) og afh\u00e6ngigheder mellem dem.\nTRIN 4: Identific\u00e9r SOLID-overtr\u00e6delser (SRP: \u00e9n fil g\u00f8r for meget).\nOutput: Din analyse som <<<DONE>>> tekst. SKRIV IKKE til filer. STOP f\u00f8rst n\u00e5r du har l\u00e6st nok til at forst\u00e5 HELE filens struktur.",
        "Plan": "Beslut hvordan filen opdeles i moduler (f.eks.: routes.py, session.py, files.py, models.py). Brug write_file() til at skrive planen som en .md fil (f.eks. refactor_plan.md). Planen skal indeholde: hvilke moduler der oprettes, hvilke funktioner/klasser der flyttes til hvert modul, og i hvilken r\u00e6kkef\u00f8lge. Systemet auto-afslutter denne fase s\u00e5 snart refactor_plan.md er skrevet \u2014 du beh\u00f6ver IKKE lave yderligere kald bagefter. <<<DONE>>> f\u00f8rst n\u00e5r planen er skrevet.",
        "Ekstraher": "Opret NYE .py modulfiler med write_file() eller extract_symbol() i.h.t. planen fra forrige fase. \ud83d\udd25 DETERMINISTISKE V\u00c6RKT\u00d8JER: Brug extract_symbol(source='api_server.py', symbol_name='FunktionsNavn', target='routes.py') for at flytte en funktion/klasse til et nyt modul \u2014 systemet kopierer symbolet inkl. imports, fjerner det fra api_server.py, OG tilf\u00f8jer en import i api_server.py. Du beh\u00f8ver KUN \u00e9t kald per symbol. Brug verify_refactor(source='api_server.py') bagefter for at bekr\u00e6fte syntaks. Du skal oprette FILER som routes.py, session_manager.py, file_handler.py osv. \u2014 IKKE write_file til refactor_plan.md (den findes allerede fra Plan-fasen og er auto-indl\u00e6st i din kontekst ovenfor). \ud83d\udd25 EFFEKTIVITETSGUIDE: Du har KUN 6 iterations. Strategi: (1) brug list_symbols F\u00d8RST for at se ALLE symboler p\u00e5 \u00e9n gang, (2) brug extract_symbol til at flytte \u00e9t symbol ad gangen (det g\u00f8r ALT arbejdet: kopi\u00e9r, fjern, tilf\u00f8j import), (3) brug write_file kun til at oprette tomme modulfiler med stubs, (4) brug verify_refactor hvis du er i tvivl om syntaks. Systemet auto-afslutter denne fase s\u00e5 snart ALLE moduler n\u00e6vnt i planen er oprettet \u2014 du beh\u00f8ver IKKE lave yderligere kald bagefter.",
        "Opdat\u00e9r": "Opdater api_server.py med remove_symbol() og add_import() \u2014 DETERMINISTISKE v\u00e6rkt\u00f8jer der ikke kr\u00e6ver LLM. Brug remove_symbol(source='api_server.py', symbol_name='FunktionsNavn') for at fjerne et symbol der allerede er flyttet til et modul. Brug add_import(source='api_server.py', module='routes', symbol='FunktionsNavn') for at tilf\u00f8je en import til et modul. Brug verify_refactor(source='api_server.py') til at tjekke syntaks. \ud83d\udd25 EFFEKTIVITETSGUIDE: Du har KUN 12 iterations. Strategi: (1) brug list_symbols F\u00d8RST for at se ALLE resterende symboler, (2) brug remove_symbol til at fjerne \u00e9t symbol ad gangen (fjerner automatisk inkl. decorators), (3) tilf\u00f8j imports med add_import, (4) brug verify_refactor til at bekr\u00e6fte. Brug edit_file KUN hvis remove_symbol/add_import ikke kan klare opgaven (f.eks. ved komplekse sektioner der ikke er rene symboler). <<<DONE>>> f\u00f8rst n\u00e5r api_server.py er syntaktisk gyldig og alle relevante imports er tilf\u00f8jet.",
        "Test": "K\u00f8r testsuiten med run_tests(). Hvis tests fejler, ret import-stier med edit_file() og genk\u00f8r. Bliv ved indtil ALLE tests best\u00e5r. Opdater issue-status til 'resolved' med update_issue_status() n\u00e5r tests best\u00e5r.",
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
        "Fix": "Tjek issue.type med read_issue(). Tjek om issuet allerede har status 'resolved' — hvis ja, redigér INGEN filer og afslut straks med <<<DONE>>>.\n\n**Hvis type = architecture:** Følg refactor-workflowet:\n  1. Analyse — Læs kildekoden med read_chunk(), forstå struktur og afhængigheder\n  2. Plan — Beslut hvordan koden opdeles/omstruktureres\n  3. Ekstraher — Opret nye filer med write_file()\n  4. Opdatér — Redigér den originale fil med edit_file() (fjern flyttet kode, tilføj imports)\n  5. Test — Kør run_tests(), ret indtil alle består\n\n**Hvis type = bug:** Ret fejlen med edit_file() — brug PRÆCIS tekst fra filen. Kør run_tests().\n\nDu må IKKE selv markere issue som resolved — det gøres i Luk Issue. Afslut med <<<DONE>>>.",
        "Luk Issue": "Bekræft at fix'et er implementeret og tests består. DU SKAL opdatere issue-status til 'resolved' med update_issue_status() — output alene lukker ikke issuet. Tilføj en PRÆCIS resolution_note: beskriv HVAD der blev ændret, HVORFOR, og bekræft at ALLE tests består. Afslut med <<<DONE>>>.",
        "read": "Read the assigned issue with read_issue(). Understand description, location, impact and proposed_fix. Do NOT read source code or edit files in this step. Use ONLY read_issue(). End with <<<DONE>>>.",
        "læs": "Læs den tildelte issue med read_issue(). Forstå beskrivelse, location, impact og proposed_fix. Du må IKKE læse kildekode eller redigere filer i dette trin. Brug KUN read_issue(). Afslut med <<<DONE>>>.",
        "afklar": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "clarify": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "fix": "Check issue.type with read_issue(). If the issue already has status 'resolved', do NOT edit any files and end immediately with <<<DONE>>>. Fix the bug with edit_file() — use EXACT text from the file. Run run_tests(). Do NOT mark issue as resolved yourself — that is done in Close Issue. End with <<<DONE>>>.",
        "luk": "Verify the fix is implemented and tests pass. YOU MUST update issue status to 'resolved' with update_issue_status() — output alone does NOT close the issue. Add a PRECISE resolution_note: describe WHAT was changed, WHY, and confirm ALL tests pass. End with <<<DONE>>>.",
        "close issue": "Verify the fix is implemented and tests pass. YOU MUST update issue status to 'resolved' with update_issue_status() — output alone does NOT close the issue. Add a PRECISE resolution_note: describe WHAT was changed, WHY, and confirm ALL tests pass. End with <<<DONE>>>.",
        "en_fix": "Check issue.type with read_issue(). If the issue already has status 'resolved', do NOT edit any files and end immediately with <<<DONE>>>. Fix the bug with edit_file() — use EXACT text from the file. Run run_tests(). Do NOT mark issue as resolved yourself — that is done in Close Issue. End with <<<DONE>>>.",
        "da_fix": "Tjek issue.type med read_issue(). Hvis issuet har status 'resolved', redigér INGEN filer og afslut straks med <<<DONE>>>. Ret fejlen med edit_file() — brug PRÆCIS tekst fra filen. Kør run_tests(). Du må IKKE selv markere issue som resolved — det gøres i Luk Issue. Afslut med <<<DONE>>>.",
        "en_clarify": "Read issue with read_issue(). Analyze if details are missing — update with update_issue_status() if needed. Read relevant source code with read_chunk() and run run_tests() to verify if the bug still exists. If already fixed: update status to 'resolved'. End with <<<DONE>>>.",
        "da_afklar": "Læs issue med read_issue(). Analyser om der mangler detaljer (præcis location, acceptance criteria, repro steps) — opdater i så fald med update_issue_status(). Læs den relevante kildekode med read_chunk() og kør run_tests() for at verificere om fejlen stadig eksisterer. Hvis fejlen ALLEREDE er løst: opdater status til 'resolved' med update_issue_status() og skriv en HONEST resolution_note. Afslut med <<<DONE>>>.",
    },
}


def refresh_skills(agent: Any) -> None:
    """refresh skills.
    
    Args:
        agent:"""
    agent._skills = SkillLoader.load_all(lang=agent.lang)


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
    }
