---
name: edit_file
keywords: [edit, ret, rediger, fix, ændr, replace, erstat, rettelse, patch,
  søg og erstat, search and replace, edit_file, symbol, function edit, rett,
  replace_in_file]
action_types: [write]
---

## edit_file — reference

```
edit_file(path, old_text="", new_text="", symbol=None)
```

| Parameter | Brug |
|-----------|------|
| `path` | Sti til filen |
| `symbol` | (BEDST) Funktionsnavn — systemet finder den via AST. Kræver `new_text`. |
| `new_text` | Hele den nye funktionskode (inkl. `def`-linjen) |
| `old_text` | (KUN faldback) Præcis 1:1 tekst fra filen. Kræver `new_text`. |

Vælg én tilstand — brug ALDRIG både `symbol` og `old_text`.

---

### AST-tilstand: `symbol` + `new_text`

```
locate(filepath="app.py", name="hej")     ← først: se præcis kode
```
```python
# locate returnerer:
def hej(navn):
    print(f"Hej {navn}")
```

```
edit_file(
    path="app.py",                        ← filen
    symbol="hej",                         ← funktion at erstatte
    new_text="""def hej(navn):            ← HELE den nye udgave
    print(f"Hej {navn}!")
    print(f"Velkommen {navn}")""")
```

`symbol` = navnet på funktionen. `new_text` = den nye `def`-linje + body. Systemet udskifter.

#### Tilføj ny funktion sidst i fil

Samme princip, men `symbol` peger på **sidste funktion**, og `new_text` indeholder BEGGE:

```
locate(filepath="app.py", name="farvel")  ← sidste funktion
```

```
edit_file(
    path="app.py",
    symbol="farvel",                       ← sidste funktion
    new_text="""def farvel(navn):          ← behold den gamle…
    print(f"Farvel {navn}")

def godnat(navn):                          ← …og tilføj den nye
    print(f"Godnat {navn}")""")
```

### Search-and-replace: `old_text` + `new_text` (faldback)

KUN til ikke-Python filer (html/json/md/txt). `old_text` skal være 1:1 byte-kopi:

```
edit_file(
    path="app.py",
    old_text='    print(f"Hej {navn}")',   ← PRÆCIS kopi
    new_text='    print(f"Hej {navn}!")')
```

### Fejlretning

- `edit_file` fejler → genlæs med `locate()` → ret `new_text` → prøv igen
- `symbol` findes ikke → fald tilbage til `old_text`+`new_text`
- ALDRIG `<<<DONE>>>` efter en fejlet edit
- NYE filer: `write_file()`, ikke `edit_file`
