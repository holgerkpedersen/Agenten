---
name: resume
keywords: resume, referat, opsummer, analyser, analyse, review, beskriv, sammenfat
action_types: [resume, analyze]
template: resume
description: Struktureret resume af en fil eller et dokument med Overblik, Noglepunkter, Konklusion og Anbefalinger.
---
# Resume / Referat

Nar du skal lave et resume af en fil:

1. **Las ALTID alle chunks forst** — brug read_chunk(chunk='FILNAVN', index=2,3,...) for at hente resten af store filer.
2. **Overblik**: Beskriv filens formal, struktur og hovedindhold pa 3-5 sætninger.
3. **Noglepunkter**: Fremhæv de vigtigste tekniske detaljer, features og centrale pointer.
4. **Konklusion**: Vurder filens kvalitet, styrker og svagheder.
5. **Anbefalinger**: Foresla konkrete forbedringer.

## Begrænsninger (OBLIGATORISK)
- **Opfind ALDRIG** detaljer der ikke står i kilden — hold dig til fakta.
- **Undga fyldord** — hver sætning skal bidrage med ny information.
- **Foresla IKKE** follow-up opgaver eller kodeændringer medmindre du bliver bedt om det.
- **Overblik: 3-5 sætninger** — ikke et helt afsnit.
- **Brug punktopstilling** til lister af distincte pointer fremfor lange afsnit.

**Vigtigt:** Analysen skal være baseret på HELE filens indhold — ikke kun første chunk.
