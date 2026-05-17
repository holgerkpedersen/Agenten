# Arkitekturplan for Key Vault System

## 📋 Systemoversigt

**Formål og Målsætning:**
At udvikle et sikkert, robust og brugervenligt system (Key Vault) til opbevaring, styring og adgang til følsomme data såsom passwords, API-nøgler og hemmeligheder. Systemet skal sikre høj dataintegritet og konfidentialitet.

**Teknologistak:**
*   **Backend:** Python 3.x, Flask (Microframework)
*   **Database:** PostgreSQL (anbefalet for robusthed) eller SQLite (til udvikling), administreret via SQLAlchemy ORM.
*   **Sikkerhed/Kryptering:** AES-256 (eller lignende stærk symmetrisk kryptering) til datalagring. Master keys skal håndteres via miljøvariabler/secrets manager.
*   **Frontend:** HTML5, CSS3, JavaScript (Vanilla JS eller et letvægtsbibliotek som Alpine.js).
*   **Templating:** Jinja2.

## 🏗️ Komponentarkitektur

Systemet vil følge en lagdelt arkitektur for at sikre Separation of Concerns (SoC) og nem vedligeholdelse.

1.  **Præsentationslaget (Presentation Layer):**
    *   Ansvar: Håndterer brugergrænsefladen, modtager HTTP-requests fra klienten og præsenterer data til brugeren.
    *   Komponenter: Flask routes, Jinja2 templates, JavaScript/AJAX calls.

2.  **Forretningslogiklaget (Business Logic Layer / Service Layer):**
    *   Ansvar: Indeholder applikationens kerneforretningsregler. Håndterer validering af input, autorisationskontrol og koordinerer dataflowet mellem præsentations- og datalaget.
    *   Komponenter: Services (f.eks. `KeyVaultService`), der kalder Repository/DAO for at manipulere data.

3.  **Datalaget (Data Access Layer / Persistence Layer):**
    *   Ansvar: Abstraherer databasen fra resten af applikationen. Sikrer, at dataopslag og -manipulation sker korrekt.
    *   Komponenter: SQLAlchemy Models, Repositories/DAOs (Data Access Objects). Dette lag er ansvarligt for krypterings-/dekrypteringsoperationer før data gemmes i databasen.

**Dataflow mellem komponenter:**
Klient $\rightarrow$ Flask Route (Præsentation) $\rightarrow$ Service Layer (Forretningslogik) $\rightarrow$ Repository/DAO (Datalag) $\rightarrow$ Database. Data returneres baglæns gennem samme sti.

## ⚙️ Flask-struktur

Projektet vil anvende et Blueprint-baseret design for at opnå modulopdeling og skalerbarhed.

*   **`app/`:** Hovedmappen.
    *   **`__init__.py`:** Initialiserer applikationen, database og blueprints.
    *   **`config.py`:** Indeholder konfigurationsindstillinger (miljøvariabler).
    *   **`models.py`:** SQLAlchemy ORM-modeller.
    *   **`services/`:** Forretningslogik (f.eks. `key_vault_service.py`).
    *   **`routes/`:** Blueprint-definitioner for specifikke funktioner (f.eks. `auth_bp`, `vault_bp`).
    *   **`static/` & `templates/`:** Frontend ressourcer.

**Request/Response Lifecycle:**
1.  Klient sender request til Flask route.
2.  Route kalder Service Layer med inputdata.
3.  Service Layer validerer og instruerer Repository Layer.
4.  Repository Layer krypterer data (hvis nødvendigt) og gemmer i DB.
5.  DB returnerer data $\rightarrow$ Repository dekrypterer $\rightarrow$ Service Layer formaterer $\rightarrow$ Route sender JSON/HTML response til klienten.

**Fejlhåndtering og Logging:**
Alle kritiske operationer (f.eks. fejl ved kryptering, uautoriseret adgang) skal logges via Python's `logging` modul. Flask's `@app.errorhandler()` vil håndtere HTTP-fejl (401, 403, 500) og præsentere standardiserede fejlmeddelelser til brugeren.

## 💾 Database Design

**ORM-modeller (SQLAlchemy):**
Vi definerer en model `KeyVaultItem`.
*   `id`: Primary Key.
*   `name`: Navnet på nøglen/passordet (f.eks. 'DatabasePassword').
*   `description`: Beskrivelse af formålet.
*   `encrypted_value`: Selve den krypterede værdi (BLOB eller TEXT).
*   `created_at`, `updated_at`: Tidsstempler.

**Relationer:**
Hvis vi skal understøtte brugere, vil der være en relation mellem `User` og `KeyVaultItem` for at styre adgangsrettigheder (Role-Based Access Control - RBAC).

**Migration-strategi (Alembic):**
Alembic bruges til at administrere databaseændringer. Migrationsfiler genereres automatisk, når ORM-modeller ændres, hvilket sikrer reproducerbarhed af databasestrukturen.

**Indeksering og Query-optimering:**
Der skal indekseres på `name` for hurtig opslagning. Da data er krypteret, vil søgninger primært ske via metadata (navn/beskrivelse), ikke selve værdien.

## 🔒 Sikkerhed (Kritisk Fokus)

**Datakryptering:**
*   Alle følsomme værdier (`encrypted_value`) skal krypteres ved lagring i databasen. Vi bruger AES-256 GCM for autentificeret kryptering.
*   Master Key (krypteringsnøglen) må **aldrig** hardcodes i kildekoden. Den skal hentes fra miljøvariabler (`os.environ`) eller en dedikeret Secrets Manager (f.eks. AWS Secrets Manager, Azure Key Vault).

**Autentifikation og Autorisation:**
*   **AuthN:** Flask-Login kombineret med JWT (JSON Web Tokens) for session management. Brugeren skal logge ind sikkert.
*   **AuthZ:** Implementering af RBAC i Service Layer, der sikrer, at en bruger kun kan se/redigere de Key Vault items, de er autoriseret til.

**Inputvalidering og Beskyttelse:**
*   **SQL Injection:** Forhindres ved brug af SQLAlchemy ORM (parameterized queries).
*   **XSS:** Forhindres automatisk via Jinja2's autoescaping.
*   **CSRF:** Implementeres ved hjælp af Flask-WTF eller lignende middleware for alle POST/PUT/DELETE requests.

## 🎨 Frontend (HTML/JS)

**Template-struktur (Jinja2):**
En base-template (`base.html`) definerer layoutet, navigationen og inkluderer statiske filer. Specifikke sider (Liste, Oprettelse, Redigering) arver fra denne.

**JS-moduler og Event-håndtering:**
*   JavaScript bruges til at håndtere brugerinteraktioner uden fuld sidegenindlæsning (AJAX/Fetch API). Dette forbedrer UX.
*   Modulært JS: Opdele logikken i separate filer (f.eks. `vault_manager.js`, `auth.js`).

**API-kommunikation:**
Frontend kommunikerer udelukkende med Flask backend via RESTful API endpoints (JSON). Dette adskiller præsentationslaget fra forretningslogikken.

## 🚀 Udviklings-workflow

*   **Miljøstyring:** Brug af `venv` og `pipenv`/`Poetry` til virtuelt miljø og afhængighedsstyring.
*   **Testing:** Enhedstest (Unit Tests) med `pytest` for Service Layer og Repository. Integrationstests for hele API-flowet.
*   **Kodekvalitet:** Implementering af linters (`flake8`), formatters (`black`) og statisk typekontrol (`mypy`) for at sikre PEP 8 compliance og koderobusthed.