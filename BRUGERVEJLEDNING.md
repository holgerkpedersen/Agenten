# 🧠 Agenten — Brugervejledning

Agenten er din egen lille AI-hjælper. Den kan læse filer, skrive kode, lave opsummeringer, analysere projekter, og meget mere. Du bestemmer hvad den skal lave — så klarer den resten.

---

## 🚀 Sådan starter du

1. Åbn en terminal (kommandoprompt)
2. Skriv `python api_server.py`
3. Åbn din browser og gå til `http://localhost:5000`

Så ser du Agentens brugerflade.

> **Første gang?** Sørg for at [LM Studio](https://lmstudio.ai) kører på din computer. Det er den "hjerne" Agenten bruger til at tænke.

---

## 🖥️ Skærmen

Når du åbner Agenten, ser du flere vinduer (paneler). Nederst på siden er der et **input-felt** — dér skriver du din besked til Agenten. Panelerne viser hvad den laver:

| Panel | Hvad det er |
|-------|-------------|
| **📁 Opgavetræ** | Oversigt over alle del-opgaver som Agenten har delt dit spørgsmål op i |
| **📡 LLM Output** | Her ser du Agentens "tanker" — hvad den svarer og hvilke værktøjer den bruger |
| **📋 Agent Log** | En detaljeret log over alt hvad Agenten gør (trin for trin) |
| **📜 Prompt Historik** | Tidligere beskeder du har sendt til Agenten |

**Hvor skriver jeg?** Nederst på skærmen er der en tekstboks og en "Send" knap. Dér skriver du hvad du vil have Agenten til at lave. Ovenover boksen er der en dropdown-menu til at vælge skabelon og sprog.

Du kan trække i panelerne for at gøre dem større eller mindre, eller klikke på `−` for at minimere et panel.

---

## 💬 Sådan taler du med Agenten

Du skriver en besked i chatten, og Agenten svarer. Det er ligesom at chatte med en ven — bare en der er rigtig god til at programmere.

**Eksempler på hvad du kan skrive:**

- "Opsummer denne fil"
- "Find fejl i koden"
- "Hvad gør den her funktion?"
- "Opret en ny todo-app med Flask"
- "Lav en branch, commit og push til GitHub"

---

## 🎯 Skabeloner

Før du skriver din besked, kan du vælge en **skabelon** i dropdown-menuen. Skabeloner fortæller Agenten hvordan den skal angribe opgaven:

| Skabelon | Hvornår bruger jeg den? |
|----------|------------------------|
| 🌳 **Fri nedbrydning** | Når du bare vil have Agenten til selv at finde ud af det |
| 📄 **Resumé** | Når du har en lang tekst, der skal opsummeres |
| 🔍 **Kodeanalyse** | Når du vil have Agenten til at gennemgå din kode for fejl |
| 📊 **Diff-analyse** | Når du vil have en risiko-vurdering af kode-ændringer |
| 🔀 **PR Agenten** | Når du vil oprette en Pull Request på GitHub |
| 🐍 **Programmeringsopgave** | Når du vil have Agenten til at skrive kode til dig |
| 🏗️ **Python Arkitektur** | Når du skal designe hvordan et program skal bygges |
| 👤 **Agenten (3 tasks)** | Når du vil have Agenten til at undersøge noget og komme med en plan |

---

## 🔧 Hvad kan Agenten gøre?

Agenten har **værktøjer** — små programmer den kan bruge til at udføre opgaver:

### 📂 GitHub værktøjer
- Opret et nyt projekt (`github_create_repo`)
- Se alle dine projekter (`github_list_repos`)
- Opret en fejlrapport (`github_create_issue`)
- Opret en Pull Request (`github_create_pr`)

### 🗂️ Git værktøjer
- Se hvilke filer du har ændret (`git_status`)
- Gem alle ændringer (`git_add_all`)
- Gem med en besked (`git_commit`)
- Upload til GitHub (`git_push`)
- Opret en ny gren (`git_create_branch`)
- Se tidligere gemte versioner (`git_log`)
- Hent andres ændringer (`git_pull`)

### 📝 Fil værktøjer
- Læs store filer stykke for stykke (`read_chunk`)
- **Skriv filer til disk** (`write_file`) — Agenten kan skrive kode direkte til dine filer!

Når Agenten skriver en `.py` fil, tjekker den automatisk:
- ✅ **Stavefejl i koden** — opdager syntaxfejl
- ✅ **Manglende pakker** — hvis koden bruger et bibliotek der ikke er installeret, skriver Agenten det i `requirements.txt`
- ✅ **Manglende web-adresser** — hvis Agenten laver både en hjemmeside (HTML) og en backend (Python), tjekker den at alle linkene passer sammen

---

## 🧠 Hvordan virker det?

1. Du skriver en besked
2. Agenten deler opgaven op i mindre del-opgaver
3. Agenten løser én del-opgave ad gangen
4. Hvis den har brug for et værktøj, bruger den det
5. Til sidst samler den alle svar og giver dig resultatet

Det hele foregår i **real-time** — du kan følge med i hvad den laver.

---

## 🌍 Sprog

Agenten taler **dansk, engelsk, spansk og kinesisk**.

Du vælger sprog i dropdown-menuen øverst. Agenten svarer på samme sprog som du skriver til den på.

---

## 💾 Gem og genoptag

Agenten gemmer automatisk alt arbejde i **sessioner**. Det betyder at:

- Du kan lukke browseren og fortsætte senere
- Du kan hente gamle sessioner frem fra listen
- Du kan omdøbe sessioner, så du kan finde dem igen

---

## 🧪 Eksempel: Få Agenten til at lave en hjemmeside

1. Vælg skabelonen **"🐍 Programmeringsopgave"**
2. Skriv: "Lav en simpel gætte-et-tal hjemmeside med Flask"
3. Agenten laver:
   - En plan
   - Koden til backend (Python/Flask)
   - Koden til frontend (HTML/JavaScript)
   - Skriver det hele til filer via `write_file`
4. Hvis der mangler pakker, skriver den dem automatisk i `requirements.txt`
5. Hvis HTML'en bruger en adresse der ikke findes i Python-koden, advarer Agenten dig

---

## 🐛 Noget virker ikke?

**Agenten svarer ikke:** Tjek at LM Studio kører på `http://localhost:1234`
**Det går for langsomt:** Prøv en mindre model i LM Studio
**Underlig kode:** Prøv at vælge en anden model — nogle modeller er bedre til at programmere end andre

---

## 📚 Mere hjælp

Læs `README.md` for teknisk dokumentation, eller spørg Agenten selv!  
Den kender sine egne værktøjer og kan forklare hvordan den virker.
