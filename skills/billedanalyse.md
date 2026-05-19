---
action_types: [analyze, write]
---

## Billedanalyse

Analyser billeder og skærmbilleder struktureret. Denne skill guider agenten gennem en systematisk gennemgang af visuelt indhold.

### Forudsætning

**Billedet SKAL være uploadet FØR "Nedbryd" klikkes.** Brug 🖼 knappen eller "Gennemse" + "Læs fil" med en billedfil (.png, .jpg, .webp, .gif, .bmp).

Hvis du klikker "Nedbryd" uden et uploadet billede, får du HTTP 400 på alle tasks — fordi LLM'en modtager en image_url uden billeddata.

### Fremgangsmåde

1. **Beskrivelse**: Start med at beskrive hvad der ses på billedet — motiv, personer, objekter, farver, layout. Giv et overordnet indtryk.
2. **Kontekst**: Hvor stammer billedet fra? Hvad er formålet? Hvilke brugere er det målrettet?
3. **Detaljer**: Gennemgå specifikke elementer — tekst, UI-komponenter, kode, tal, datoer, fejlmeddelelser.
4. **Vurdering**: Vurder kvalitet og indhold. Hvad fungerer? Hvad kan forbedres? Er der fejl?
5. **Eksport**: Gem altid analysen i en .md fil med write_file (`./exports/billedanalyse_{timestamp}.md`).

### Værktøjer

- `add_image(path)` — tilføj billede til analysekonteksten (agenten kalder denne automatisk)
- `write_file(path, content)` — gem analysen
- `read_chunk(chunk, index)` — læs filchunks hvis nødvendigt
- `list_chunks()` — se tilgængelige filer

### Format

Analysen skal struktureres som Markdown:
```markdown
# Billedanalyse

## Beskrivelse
...

## Kontekst
...

## Detaljer
...

## Vurdering
...
```

### Common mistakes (undgå disse)

| Fejl | Symptom | Løsning |
|------|---------|---------|
| Nedbryd uden uploadet billede | `[ERROR: HTTP 400]` på alle tasks | Upload billede FØRST via 🖼 eller Gennemse+Læs fil |
| Forkert model (text-only) | HTTP 400 | Brug en vision-model (gemma-4, qwen-vl, llava) |
| `data:image/...` format til gemma | `'url' field must be a base64 encoded image` | `llm_wrapper.py` håndterer dette automatisk via IMAGE_FORMATS |
| Billeder efter tekst i prompt | HTTP 400 (gemma 4) | `llm_wrapper.py` håndterer dette automatisk — billeder før tekst |

### Model-anbefaling

| Model | Vision | Note |
|-------|--------|------|
| `google/gemma-4-26b-a4b` | ✅ | Virker, kræver raw_b64 + billeder før tekst |
| `google/gemma-4-e4b` | ✅ | Virker, samme krav |
| `qwen/qwen3.5-9b` | ❌ | Text-only |

### Session-håndtering

- Billeder er **session-scoped** — de gemmes med sessionen og indlæses ved session-skift
- Auto-save efter upload, rydning, og sletning af billeder
- Ny session = tomme billeder
