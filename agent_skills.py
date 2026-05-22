import re
from lang import t
from i18n import K
from skill_loader import SkillLoader


TEMPLATE_TOOLS = {
    "resume": ["list_chunks", "read_chunk", "list_files"],
    "kodeanalyse": ["list_chunks", "read_chunk", "list_files", "create_issue"],
    "diffanalyse": ["list_chunks", "read_chunk", "git_diff", "git_log", "list_files", "create_issue"],
    "fri": None,
    "agenten": [
        "list_chunks",
        "read_chunk",
        "list_files",
        "github_create_pr",
        "git_status", "git_add_all", "git_commit", "git_push",
        "git_diff", "git_log",
        "git_create_branch", "git_current_branch", "git_pull", "git_checkout",
        "git_remote_status"
    ],
    "programmering": ["list_chunks", "read_chunk", "write_file", "add_image", "list_files", "create_issue"],
    "python-arkitektur": ["list_chunks", "read_chunk", "write_file", "list_files", "create_issue"],
    "billedanalyse": ["add_image", "write_file", "list_chunks", "read_chunk", "list_files", "create_issue"],
    "bugfix": ["read_issue", "update_issue_status", "run_tests", "create_refactor_issue", "create_issue", "list_chunks", "read_chunk", "write_file", "edit_file", "list_files"],
    "refactor": ["list_chunks", "read_chunk", "write_file", "edit_file", "run_tests", "list_files", "create_issue"],
    "testgenerering": ["list_chunks", "read_chunk", "write_file", "edit_file", "run_tests", "list_files", "create_issue", "update_issue_status"],
}

TEMPLATE_TASK_TOOLS = {
    "agenten": {
        "branch": ["git_current_branch", "git_create_branch", "git_branch_list", "git_checkout", "git_remote_status", "git_pull"],
        "commit": ["git_add_all", "git_commit", "git_status", "git_diff", "git_log"],
        "push": ["git_push", "git_remote_status"],
        "pull request": ["github_create_pr", "git_remote_status", "git_diff", "git_log"],
    },
    "bugfix": {
        "analyse": ["read_issue", "list_files", "list_chunks", "read_chunk", "run_tests", "create_issue", "update_issue_status"],
        "test": ["write_file", "list_files", "run_tests", "list_chunks", "read_chunk", "create_issue"],
        "implementering": ["edit_file", "list_files", "list_chunks", "read_chunk", "run_tests", "create_issue"],
        "verifikation": ["run_tests", "edit_file", "list_files", "list_chunks", "read_chunk", "create_issue"],
        "opdatering": ["update_issue_status", "list_files", "list_chunks", "read_chunk", "create_issue"],
    },
    "refactor": {
        "analyse": ["list_files", "list_chunks", "read_chunk"],
        "plan": ["list_files", "list_chunks", "read_chunk"],
        "ekstraher": ["write_file", "list_files", "list_chunks", "read_chunk"],
        "opdatér": ["edit_file", "list_files", "list_chunks", "read_chunk"],
        "test": ["run_tests", "list_files", "list_chunks", "read_chunk"],
    },
    "testgenerering": {
        "analyse": ["list_files", "list_chunks", "read_chunk", "run_tests", "create_issue"],
        "test": ["write_file", "list_files", "run_tests", "list_chunks", "read_chunk", "create_issue"],
        "implementering": ["edit_file", "list_files", "list_chunks", "read_chunk", "run_tests", "create_issue"],
        "verifikation": ["run_tests", "edit_file", "list_files", "list_chunks", "read_chunk", "create_issue"],
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
        "Kravanalyse": "Analyser kravene grundigt. Identific\u00e9r funktionelle og ikke-funktionelle krav, input/output, og eventuelle begr\u00e6nsninger. Beskriv hvad systemet skal kunne.",
        "Arkitekturdesign": "Design systemarkitekturen: komponenter, moduler, dataflow og afh\u00e6ngigheder. Overvej relevante design patterns og SOLID-principper. Tegn arkitekturen med tekst.",
        "Implementeringsplan": "Planl\u00e6g implementeringen: hvilke filer skal oprettes, i hvilken r\u00e6kkef\u00f8lge, og hvad skal hver fil indeholde. Overvej teststrategi og edge cases.",
        "Sikkerhedsanalyse": "Analyser sikkerhedsaspekter: inputvalidering, autentifikation, kryptering, h\u00e5ndtering af f\u00f8lsomme data (passwords, keys). F\u00f8lg OWASP best practices og princip om mindste rettighed.",
        "Kodeimplementering": "Implement\u00e9r koden baseret p\u00e5 arkitekturdesign og implementeringsplan. Brug write_file til at oprette/redigere hver fil. Skriv ren, vedligeholdelsesvenlig kode med korrekt fejlh\u00e5ndtering og logging.",
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
        "Analyse": "Læs issue med read_issue(). Forstå hvad bug'en er og hvilken kode der skal ændres. Læs den relevante kildekode med read_chunk(). Forstå rodårsagen. Verificér at fejlen stadig findes — hvis koden allerede er rettet, opdater issue-status til 'resolved' med update_issue_status() og afslut med <<<DONE>>>. SKRIV IKKE til filer — kun analyse. Opret et issue med create_issue() hvis du opdager en ny fejl i koden eller data der skal rettes.",
        "Test (Red)": "Skriv en pytest der fanger bug'en. Opret en ny testfil med write_file (testfilen findes ikke i forvejen). Kør testen med run_tests() — den SKAL fejle (rød fase). Hvis testen består (i stedet for at fejle), er bug'en allerede fikset — opdater issue-status til 'resolved' og afslut med <<<DONE>>>.",
        "Implementering": "Ret kildekoden med den mindst mulige ændring. Brug read_chunk til at læse filen og find den PRÆCISE tekst der skal ændres — kopiér teksten direkte ind i edit_file's old_text med samme indentering, samme quotes, samme linjeafslutninger. Brug IKKE write_file — filen findes allerede.",
        "Verifikation (Green)": "Kør testen igen med run_tests() — den SKAL bestå (grøn fase). Kør HELE testsuiten med run_tests() for at verificere ingen regressions.",
        "Opdatering": "Opdater issue-status til 'resolved' med update_issue_status(). Tilføj en kort resolution_note om hvad der blev fikset.",
    },
    "refactor": {
        "Analyse": "Læs filen med read_chunk(). Forstå alle funktioner, klasser, imports og deres ansvar. Identificér grænseflader og afhængigheder mellem komponenter. SKRIV IKKE til filer — kun analyse.",
        "Plan": "Beslut hvordan filen opdeles i moduler. F.eks.: ét modul per klasse, ét modul per ansvarsområde, fælles imports i en base-modul. Overvej SOLID-principperne.",
        "Ekstraher": "Opret nye modulfiler med write_file() — disse er NYE filer. Flyt relevant kode til hvert modul. Bevar samme funktionalitet — bare omorganiseret.",
        "Opdatér": "Opdater den originale fil med edit_file(): fjern den kode der blev flyttet, tilføj import af nye moduler. Brug IKKE write_file — den originale fil findes allerede.",
        "Test": "Kør testsuiten med run_tests() for at verificere at intet er gået i stykker. Hvis tests fejler, ret import-stier og genkør.",
    },
    "testgenerering": {
        "Analyse": "Læs filen med read_chunk(). Forstå alle klasser, funktioner, metoder og imports. Identificér hvilke der allerede har tests og hvilke der mangler. Opret et issue med create_issue() hvis du finder kode der mangler tests.",
        "Test (Red)": "Skriv pytest-tests for den manglende dækning. Opret en NY testfil med write_file (testfilen må ikke findes i forvejen). Kør testen med run_tests() — den SKAL bestå (grøn fase). Hvis testen fejler, ret koden med edit_file og genkør.",
        "Implementering": "Hvis produktionskoden skal ændres for at gøres testbar, brug edit_file til målrettede ændringer. Brug IKKE write_file — produktionsfilen findes allerede.",
        "Verifikation (Green)": "Kør HELE testsuiten med run_tests() for at verificere ingen regressions. Opdater issue-status til 'resolved' med update_issue_status() hvis et TST-issue blev løst.",
    },
}


def refresh_skills(agent):
    agent._skills = SkillLoader.load_all(lang=agent.lang)


def match_skills(agent, prompt):
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


def has_matching_intent(agent, skill):
    intent = skill.get("template") or skill.get("intent")
    return not agent.active_template or intent == agent.active_template or skill.get("base")


def format_skills_for_prompt(agent):
    if not agent._active_skills:
        return ""
    lines = ["\n## \U0001f4cb Retningslinjer (ikke v\u00e6rkt\u00f8jer)\n"]
    for s in agent._active_skills:
        tag = "BASE" if s.get("base") else "MATCH"
        lines.append(f"- **{s['name']}** [{tag}]: {s.get('description', '')[:120]}")
    return "\n".join(lines)


def get_templates(agent):
    lang_instr = t(K.ANSWER_IN, agent.lang)
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
            "prompt": t(K.TP_FRI, agent.lang),
            "fallback": None
        },
        "agenten": {
            "name": t(K.T_AGENTEN, agent.lang),
            "prompt": t(K.TP_AGENTEN, agent.lang).replace("{lang_instruction}", lang_instr),
            "fallback": t(K.TF_AGENTEN, agent.lang),
        },
        "programmering": {
            "name": t(K.T_PROGRAMMERING, agent.lang),
            "prompt": t(K.TP_PROGRAMMERING, agent.lang),
            "fallback": t(K.TF_PROGRAMMERING, agent.lang),
        },
        "python-arkitektur": {
            "name": t(K.T_PYTHON_ARKITEKTUR, agent.lang),
            "prompt": t(K.TP_PYTHON_ARKITEKTUR, agent.lang),
            "fallback": t(K.TF_PYTHON_ARKITEKTUR, agent.lang),
        },
        "billedanalyse": {
            "name": t(K.T_BILLEDANALYSE, agent.lang),
            "prompt": t(K.TP_BILLEDANALYSE, agent.lang),
            "fallback": t(K.TF_BILLEDANALYSE, agent.lang),
        },
        "bugfix": {
            "name": t(K.T_BUGFIX, agent.lang),
            "prompt": t(K.TP_BUGFIX, agent.lang),
            "fallback": t(K.TF_BUGFIX, agent.lang),
        },
        "refactor": {
            "name": t(K.T_REFACTOR, agent.lang),
            "prompt": t(K.TP_REFACTOR, agent.lang),
            "fallback": t(K.TF_REFACTOR, agent.lang),
        },
        "testgenerering": {
            "name": t(K.T_TESTGENERERING, agent.lang),
            "prompt": t(K.TP_TESTGENERERING, agent.lang),
            "fallback": t(K.TF_TESTGENERERING, agent.lang),
        },
    }
