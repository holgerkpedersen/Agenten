## Systemoversigt

### Formål og målsætning
Projektet (DEX) er en Python-baseret version af Signum DEX Dashboard. Formålet er at fungere som et middleware-lag, der henter data fra Signum blockchain-netværket og præsenterer det i et brugervenligt dashboard via et RESTful API og et webinterface.

### Teknologistak
- **Sprog:** Python 3.10+
- **Backend Framework:** Flask
- **API Kommunikation:** HTTPX (asynkron support)
- **Frontend:** HTML5, CSS3 (Tailwind/Bootstrap), JavaScript (Vanilla/Fetch API)
- **Templating:** Jinja2
- **Miljøstyring:** python-dotenv
- **Server:** Gunicorn (produktion) / Flask Dev Server (udvikling)

## Komponentarkitektur

### Modulopdeling og ansvar
- **API Layer (Blueprints):** Håndterer HTTP requests, routing og validering af input.
- **Service Layer:** Indeholder forretningslogik og kommunikation med Signum Node via HTTPX. Dette lag isolerer blockchain-logikken fra API-laget.
- **Data/Model Layer:** Definerer datastrukturer (Pydantic eller Dataclasses) for tokens, trades og markedsdata.
- **Presentation Layer:** Jinja2 templates og statiske filer (JS/CSS).

### Lagdelt struktur
1. **Præsentation:** HTML/JS modtager data fra API'et.
2. **Forretningslogik:** Flask Blueprints modtager requests $\rightarrow$ kalder Services $\rightarrow$ returnerer JSON/Templates.
3. **Data:** Services henter rådata fra den eksterne Signum Node.

## Flask-struktur

### Blueprint-moduler
Projektet vil benytte Blueprints for at spejle den eksisterende struktur:
- `api_markets`: Markedsdata
- `api_tokens`: Token information
- `api_trades`: Handelsdata
- `api_holders`: Holder statistikker
- `api_aliases`: Alias management

### Request/Response Lifecycle
1. Client sender request $\rightarrow$ 2. Flask Router identificerer Blueprint $\rightarrow$ 3. Service layer udfører logik/fetch $\rightarrow$ 4. Response returneres som JSON eller renderet HTML.

## Database design

*Bemærk: Det nuværende projekt er primært en proxy. Hvis caching eller lagring af aliases kræves, implementeres følgende:* 
- **ORM:** SQLAlchemy
- **Migration:** Alembic
- **Model:** En letvægts SQLite/PostgreSQL database til persistens af brugerdefinerede aliases.

## Sikkerhed

- **Miljøvariabler:** Alle secrets (Node URL, API keys) gemmes i `.env` og tilgås via `os.getenv`.
- **CORS:** Konfigureret via `Flask-CORS` for at tillade kontrolleret adgang fra frontend.
- **Input Validering:** Brug af Pydantic til at sikre, at data fra blockchainen eller brugere er korrekte.
- **Beskyttelse:** Standard beskyttelse mod XSS i Jinja2 og SQL injection via SQLAlchemy.

## Frontend (HTML/JS)

- **Template-struktur:** Centraliseret `base.html` med blokke (`{% block content %}`) for at undgå kode-duplikering.
- **Statisk indhold:** CSS og JS placeres i `/static` mappen.
- **API-kommunikation:** Moderne `fetch()` API anvendes til asynkront at opdatere dashboardet uden page reloads.

## Udviklings-workflow

- **Virtuelt miljø:** `venv` eller `conda` bruges til isolation af dependencies.
- **Afhængighedsstyring:** `requirements.txt` for nem installation.
- **Testing:** `pytest` til unit tests af services og integrationstests af API endpoints.
- **Kodekvalitet:** 
  - `black` for formatering
  - `flake8` for linting
  - `mypy` for type checking (type hints er obligatoriske i service-laget)
