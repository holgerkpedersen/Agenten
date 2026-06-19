# Agenten

Planificador de tareas de IA danés con uso de herramientas — descompone prompts en árboles de tareas, analiza archivos, refactoriza código y realiza operaciones autónomamente mediante LLM.

## 🚀 Inicio rápido

```bash
pip install -r requirements.txt
cp .env.example .env   # Edita .env con tu token de GitHub
python api_server.py   # Abre http://localhost:5000
```

**Requisitos:** [LM Studio](https://lmstudio.ai) ejecutándose en `localhost:1234` con un modelo compatible — or [OpenCode Go](https://opencode.ai) with `OPENCODE_API_KEY`.
**Visión:** Usa un modelo compatible con visión (Gemma 4, Qwen-VL, Llava).  
**Salida inicial:** `🕐 Startet: 2026-05-19 15:21:30 | api_server=15:21:30 | llm=15:15:05` — verifica la versión.

## 📁 Estructura del proyecto

```
agent_core.py         # Fachada del agente: init, registro de herramientas, decompose, execute, delegados
agent_tasks.py        # Ejecución de tareas: solve_task_stream, solve_task, handle_tool_call
agent_tree.py         # Operaciones de árbol: parse, create_fallback_tree, record_outcome, evolve_if_needed
agent_files.py        # Operaciones de archivos: lectura/escritura/chunk, escaneo de carpetas (.env excluido)
agent_skills.py       # Coincidencia de habilidades, constantes de plantillas (TEMPLATE_TOOLS, TEMPLATE_TASK_TOOLS)
agent_autoresearch.py # Investigación automática: clasificar fallos, construir solución propuesta, crear issues CORE, reintentar
agent_phase_checks.py # Comprobaciones deterministas de fase: file_exists, files_from_plan, tool_called, tests_pass
agent_wta.py          # Arbitraje ponderado de herramientas: rank_tool_calls, puntuación Laplace, análisis de secuencias
agent_issues.py       # Herramientas de issues: read_issue, update_issue_status, create_issue, detección de tamaño
agent_git.py          # Flujo de trabajo Git/PR: is_pr_workflow, extract_branch_name, verify_pr_step
api_server.py         # API Flask: streaming SSE, sesiones, carga de imágenes, versión, endpoint de issues
llm_wrapper.py        # Cliente HTTP de LM Studio (chat + streaming + visión/codificación de imágenes)
tools.py              # Tool/ToolRegistry — marco de herramientas (parse_response, build_system_prompt)
task_tree.py          # Estructuras de datos TaskTree / TaskNode
config.py             # Constantes centrales (CHUNK_SIZE, timeout, max_tokens)
git_ops.py            # Operaciones Git + archivos (write_file, edit_file con validación)
github_wrapper.py     # API de GitHub: repos, issues, PRs
session_manager.py    # Persistencia de sesiones (JSON), bloqueo de hilos
web_searcher.py       # Web scraping de DuckDuckGo
ddg_search.py         # Búsqueda DuckDuckGo (fallback)
flow_builder.py       # Constructor de flujo de prompts
module_builder.py     # Constructor dinámico de módulos (experimental)
model_manager.py      # Listador de modelos API REST OpenAI + LM Link
skill_loader.py       # Sistema de habilidades — frontmatter, puntuación de palabras clave, coincidencia de plantillas
skill_evolution.py    # SkillFlow — seguimiento de resultados, análisis de evolución (Retain/Refine/Prune/Generate)
skill_tracker.py      # Registro de resultados por habilidad con success_rate
lang.py               # Traducciones (da/en/es/zh)
i18n.py               # Claves de internacionalización (enum K)
AGENTS.md             # Base de conocimiento — errores, correcciones, flujo de depuración
BRUGERVEJLEDNING.md   # Guía de usuario (danés)
static/index.html     # UI de navegador con paneles de arrastre/redimensión, selector de plantillas, vista previa de imágenes, visor de issues
core_analytics.py    # Seguimiento de resultados de herramientas/pruebas, puntos clave, resúmenes
instructions/        # Instrucciones de sección por plantilla (JSON, 12 plantillas)
tests/                # 739 pruebas (pytest)
sessions/             # Persistencia de sesiones JSON (guardar/cargar/eliminar)
skills/               # Habilidades en markdown con frontmatter
```

## 🎯 Plantillas

Selecciona una plantilla en el menú desplegable antes de la descomposición — el LLM recibe secciones fijas:

| Plantilla | Descripción |
|-----------|-------------|
| 🌳 **Descomposición libre** | El LLM determina el árbol de tareas dinámicamente (3-6 tareas principales, máx 2 niveles) |
| 📄 **Resumen** | Descripción general → Puntos clave → Conclusión → Recomendaciones |
| 🔍 **Análisis de código** | Propósito → Imports → Arquitectura → Calidad del código → Seguridad |
| 📊 **Análisis de diff** | Git log + diff → Evaluación de riesgos → Recomendaciones |
| 🔀 **PR Agent** | Rama → Commit → Push → Pull Request (flujo de PR automatizado) |
| 💻 **Tarea de programación** | Requisitos → Arquitectura → Plan de implementación → Seguridad → Código |
| 🏗️ **Arquitectura Python** | Planificación de arquitectura con salida `write_file` a `./docs/arkitektur.md` |
| 🖼️ **Análisis de imagen** | Descripción → Contexto → Detalles → Evaluación → Exportar (.md) |
| 🔧 **Refactorización** | Análisis → Plan → Extraer → Actualizar → Probar (refactorización SOLID) |
| 🧪 **Generación de pruebas** | Análisis → Prueba (Rojo) → Implementación → Verificación (Verde) |
| 🐛 **Bugfix (TDD)** | Análisis → Prueba (Rojo) → Implementación → Verificación (Verde) → Actualización |
| 📋 **Issue Handler** | Leer → Analizar → Corregir → Verificar → Actualizar estado |

**El análisis de imagen requiere:** Sube una imagen con el botón 🖼 **antes** de hacer clic en Descomponer. Las imágenes WebP se convierten automáticamente a MIME `image/png` para compatibilidad con gemma.

## 🔧 Herramientas

El agente puede realizar operaciones del sistema mediante marcadores `<<<TOOL>>>` (35 herramientas):

| Herramienta | Acción |
|-------------|--------|
| `plan_phase` | Crear plan detallado de tareas con llamadas a herramientas y pasos |
| `create_todo` | Añadir nueva tarea al plan personal del LLM |
| `update_todo` | Marcar tarea como completada o actualizar texto |
| `delete_todo` | Eliminar tarea del plan |
| `list_todos` | Mostrar criterios de éxito del Agent y plan de acción del LLM |
| `list_chunks` | Lista todos los archivos cargados |
| `read_chunk` | Lee un fragmento de un archivo grande |
| `locate` | Encuentra la línea actual de función/clase/variable PYTHON vía AST — NO es nombre de herramienta |
| `write_file` | Crea un NUEVO archivo (rechaza sobrescribir .py existente — usa edit_file) |
| `edit_file` | Búsqueda y reemplazo en archivos existentes (con verificación de sintaxis) |
| `list_files` | Lista archivos en un directorio (con filtro de patrón y profundidad máxima) |
| `create_issue` | Crea un nuevo issue |
| `create_refactor_issue` | Crea issue de refactorización para archivos grandes |
| `read_issue` | Lee un issue |
| `update_issue_status` | Actualiza el estado de un issue |
| `run_tests` | Ejecuta pytest y devuelve resultados |
| `add_image` | Añade imagen al contexto (codificada en base64) |
| `github_create_repo` | Crea un repositorio de GitHub |
| `github_list_repos` | Lista tus repositorios |
| `github_create_issue` | Crea un issue en GitHub |
| `github_create_pr` | Crea una pull request |
| `git_status` | Muestra archivos modificados |
| `git_add_all` | Prepara todos los cambios |
| `git_commit` | Confirma con un mensaje |
| `git_push` | Empuja al remoto |
| `git_set_remote` | Establece la URL del remoto |
| `git_remote_status` | Verifica la configuración remota |
| `git_diff` | Muestra diferencias entre commits |
| `git_log` | Muestra commits recientes |
| `git_create_branch` | Crea una nueva rama |
| `git_current_branch` | Muestra la rama actual |
| `git_branch_list` | Lista todas las ramas |
| `git_pull` | Trae cambios del remoto |
| `git_checkout` | Cambia a una rama |

## 🖼️ Visión / Análisis de imagen

Sube imágenes con el botón 🖼 o "Examinar" + "Leer archivo". Soporta `.png`, `.jpg`, `.webp`, `.gif`, `.bmp`.

**Las imágenes tienen ámbito de sesión** — se guardan con la sesión, se cargan al cambiar de sesión, se limpian al crear una nueva.

### Compatibilidad de modelos

| Modelo | Formato | Tipo JSON |
|--------|---------|-----------|
| **Gemma 4** (26b/e4b) | `data:image/png;base64,...` | `image_url` |
| Qwen / GPT / Llava | `data:image/png;base64,...` | `image_url` |

> **Importante:** El MIME `image/webp` es rechazado por Gemma 4 en LM Studio — se mapea automáticamente a `image/png`. Las imágenes se colocan **antes** del texto en el array de contenido (requisito de Gemma).

Consulta `skills/vision_models.md` para la matriz de compatibilidad completa y `AGENTS.md` para el flujo de depuración.

## ✅ Validación

`write_file` y `edit_file` realizan validaciones automáticas en los archivos escritos:

| Validación | Cuándo | Descripción |
|------------|--------|-------------|
| **Verificación de sintaxis** | archivos `.py` | `ast.parse()` — evita escribir archivos con errores de sintaxis |
| **Verificación de dependencias** | archivos `.py` | Escanea imports contra `requirements.txt` — actualiza automáticamente |
| **Discrepancia de rutas** | `.py/.html/.js` | Compara URLs de frontend/backend — devuelve `route_warnings` |
| **Protección de sobrescritura** | archivos `.py` | `write_file` rechaza sobrescribir archivos existentes — usa `edit_file` |

## 🔐 Seguridad

- **Token en `.env`**: Token de GitHub SOLO en `.env` (no en código, no en git)
- **`.env` nunca escaneado**: Los archivos `.env` están excluidos del escaneo de carpetas y de `read_file_content`
- **Inyección de prompts**: Los marcadores `<<<TOOL>>>` y `<<<DONE>>>` se eliminan de la entrada del usuario. Sanitización adicional mediante `_sanitize_prompt()`
- **Verificación de sintaxis antes de escribir**: `write_file` y `edit_file` validan la sintaxis de Python ANTES de escribir
- **Solo herramientas registradas**: `ToolRegistry.execute()` rechaza nombres de herramientas desconocidos
- **Restricciones de herramientas por fase**: Cada fase de una plantilla solo tiene acceso a las herramientas relevantes
- **Autenticación API key**: Protección opcional con clave API en endpoints `/api/*` (configurar `AGENT_API_KEY`)
- **Validación de magic bytes**: La carga de imágenes valida magic bytes (no solo la extensión)
- **Seguridad de subprocesos**: Los comandos de Git usan argumentos de lista (sin shell)
- **LM Studio**: Se ejecuta localmente — no se envían datos externamente (excepto la API de GitHub)

## 🤖 Configuración de LM Studio

1. Descarga [LM Studio](https://lmstudio.ai)
2. Descarga un modelo:
   - **Visión**: `google/gemma-4-26b-a4b` o `gemma-4-e4b`
   - **Texto**: `qwen/qwen3.6-35b-a3b` o `qwen3-30b-a3b`
3. Inicia el servidor en `http://localhost:1234`
4. Establece la longitud de contexto al menos 8192

## 🏗️ Arquitectura

```
Navegador (index.html)
    │ SSE (EventSource)
    ▼
API Flask (api_server.py)
    │
    ├── decompose() → agent_core.decompose_prompt()
    │       ├── agent_skills.get_templates() / match_skills()
    │       ├── agent_files.get_folder_context() / get_single_file_context()
    │       ├── agent_tree.parse_tree_from_llm() / create_fallback_tree()
    │       └── LLM (descomposición)
    │
    ├── execute_stream() → agent_core.solve_task_stream()
    │       ├── agent_tasks.solve_task_stream() → bucle LLM + Herramientas
    │       ├── agent_tasks.handle_tool_call()
    │       ├── agent_git.verify_pr_step()
    │       ├── agent_tree.record_outcome()
    │       └── Saltar nodo raíz cuando existen hijos (sin re-ejecución redundante)
    │
    ├── /api/image/* — subir/listar/limpiar/eliminar
    ├── /api/issues — listar todos los issues rastreados
    ├── /api/version — versión del servidor + marcas de tiempo
    └── sessions/ (persistencia JSON)
```

**Bucle de herramientas**: `solve_task_stream` → LLM → analizar respuesta → HERRAMIENTA: ejecutar → alimentar resultado → LLM → ... → `<<<DONE>>>`

**Flujo de PR**: `agent_git.verify_pr_step()` exige rama → commit → push → PR en el orden correcto.

**Habilidades**: `skill_loader.py` carga `skills/*.md` con frontmatter. `agent_skills.match_skills()` puntúa prompts y activa habilidades relevantes. `skill_evolution.py` analiza resultados y sugiere Retain/Refine/Prune/Generate.

**SkillFlow**: `skill_tracker.py` registra resultados por habilidad. Después de 15+ resultados, `agent_tree.evolve_if_needed()` activa el análisis de evolución automático.

**Seguimiento de versiones**: El inicio del servidor muestra `🕐 Startet:` + `📦 llm=HH:MM:SS`. `/api/version` devuelve todas las marcas de tiempo de archivos.

## 📝 Características

- **Tareas impulsadas por LLM**: El LLM crea y gestiona su propio plan mediante `plan_phase`, `create_todo`, `update_todo`, `delete_todo`, `list_todos`. Se muestra en el panel de tareas y en línea en la salida del LLM.
- **Tareas LLM auto-generadas**: La plantilla refactor genera tareas por módulo automáticamente desde `refactor_plan.md`.
- **refactor_analyse.md**: La fase de análisis guarda su salida en `refactor_analyse.md`, que se carga automáticamente en la fase Plan — ahorra 3-5 iteraciones al evitar releer símbolos/funciones.
- **Instrucciones agnósticas**: Las instrucciones de sección usan `{source_file}` en lugar de `api_server.py`.
- **La fase Opdatér genera patrones automáticamente**: Los patrones regex `code_contains` se generan dinámicamente desde los nombres de módulo en `refactor_plan.md`.
- **Deshacer limpia el estado de ejecución**: Los archivos de sesión se limpian (agent_log, execution_log, llm_todos) antes del git reset.
- **Bugfix autónomo**: 🐛 Plantilla Bugfix (TDD) → Análisis → Prueba → Implementación → Verificación → Actualización
- **Refactorización autónoma**: 🔧 Refactor → Análisis → Plan → Extraer → Actualizar → Probar
- **Generación de pruebas**: 🧪 Genera pruebas para clases/funciones/métodos no probados
- **Análisis de imágenes**: Subir → Descomponer → Análisis estructurado de 5 fases → Exportar .md
- **Soporte de visión**: Detección automática de modelos, adaptación de formato (Gemma requiere raw_b64 + imágenes antes del texto)
- **Visor de issues**: Botón 🐛 Issues muestra todos los issues rastreados con detalles y acción "Usar como tarea"
- **Issue Handler**: 📋 Flujo de trabajo automatizado para corregir issues (leer → analizar → corregir → verificar)
- **Edición precisa de archivos**: `edit_file` búsqueda y reemplazo en lugar de reescrituras completas
- **Autodescubrimiento**: La herramienta `create_issue` reporta nuevos errores/issues durante el análisis
- **Sesiones**: Guardar/cargar/renombrar/eliminar — almacenamiento JSON persistente con escritura atómica
- **Análisis de archivos**: Subir archivos, escaneo de carpetas (.env excluido), fragmentación automática
- **Streaming**: Salida SSE en tiempo real con alternar pensamiento y botón de detener
- **Cascada de resultados**: Los resultados de tareas anteriores se alimentan a la siguiente tarea
- **Paneles de arrastre/redimensión**: Diseño libre con maximizar/minimizar
- **Exportación Markdown**: Vista previa + descarga de informes de sesión
- **Multilenguaje**: UI e instrucciones LLM en danés, inglés, español y chino
- **Restricciones de herramientas por fase**: Cada fase obtiene solo las herramientas relevantes (ej. Análisis = solo lectura)
- **Timeout**: La ejecución de tareas se cancela después de 30 minutos (EXECUTION_TIMEOUT)
- **Auto-DONE**: Evita bucles infinitos de herramientas después de 10-15 iteraciones
- **Análisis JSON robusto**: `json.JSONDecoder().raw_decode()` maneja la salida de IA
- **Soporte LM Link**: Modelos API REST compatibles con OpenAI
- **Context-CoT integration**: Extract-first guidance (LLM debe resumir el contexto antes de las herramientas), anti-leakage read_issue (solo problema por defecto, sugerencias en solicitud), validación basada en rúbrica por habilidad (checks binarios → retry)
- **Soporte OpenCode Go**: Establece `OPENCODE_BASE_URL` + `OPENCODE_API_KEY` para usar OpenCode Go en lugar de LM Studio
- **Llamadas nativas de funciones**: El parámetro nativo `tools` de OpenAI se envía con completaciones de chat — el modelo devuelve tool_calls estructurados en lugar del análisis de marcadores
- **Corrección persistencia de sesiones**: Fuga de `current_session_id` entre archivos de prueba corregida, debounce en `_save_session_data` eliminado (siempre guarda al final SSE), serialización de árbol ahora incluye el campo `result`
- **Checks de fases y autoavance**: Criterios de éxito determinísticos para las fases. El sistema se completa automáticamente cuando todos los módulos existen o la planificación está escrita.
- **Límites de iteraciones para plantilla Refactor**: Presupuesto más alto (15-12 iteraciones) para manejar grandes cargas de trabajo de refactorización.
- **739 pruebas**: Suite pytest que cubre todos los módulos

## 📋 Requisitos

```
flask>=3.1.3
flask-cors>=6.0.2
requests>=2.33.1
beautifulsoup4>=4.14.3
python-dotenv>=1.2.2
openai>=1.0.0
```

## 🔄 Flujo de trabajo Git

```bash
git add -A
git commit -m "descripción"
git push
```

Usa la plantilla **🔀 PR Agent** para flujo de PR automatizado, **💻 Tarea de programación** para generación de código, **🖼️ Análisis de imagen** para tareas de visión, **🔧 Refactorización** para reestructuración de código, y **🐛 Bugfix (TDD)** para corrección de errores.
