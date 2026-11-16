# AI 3D Studio

Aplikacja webowa do tworzenia, edycji i eksportu modeli 3D z pomocą AI. Model powstaje na żywo w prawdziwym silniku 3D, a nie w atrapie.

- **CAD Mode**: precyzyjne bryły B-Rep na **OpenCascade (CadQuery)**. Wymiary w mm, parametry użytkownika, szkice, extrude, revolve, boolean, fillet, chamfer, shell, hole, pattern, mirror, pomiary, logo wytłaczane lub grawerowane z obrazu. Eksport STEP, 3MF (z kolorami), STL i innych formatów.
- **Game Mode**: kolorowe assety PBR (trimesh + manifold3d). Loft, lathe, tube i prymitywy z UV, złożone komponenty (koło, postać, drzewo), proceduralne mapy PBR (albedo, normal, roughness, metallic, AO), naklejki rzutowane na powierzchnię, LOD, decymacja, optymalizacja pod Roblox, Unity, Unreal i web. Eksport GLB, FBX, OBJ, glTF, USDZ.
- **Live Build**: każdy krok to prawdziwa operacja silnika. Po każdym kroku backend renderuje nowy GLB i wysyła go przez **WebSocket**. Nowe elementy pojawiają się w viewporcie z animacją. Masz etapy, log, Stop, Undo/Redo i wznowienie zadania po błędzie.
- **AI Provider Manager**: Anthropic Claude, OpenAI, Google Gemini, Ollama i dowolne API zgodne z OpenAI, przez bezpośrednie REST API, niezależnie od MCP. Klucze są szyfrowane na serwerze i nigdy nie trafiają do przeglądarki. Różnym zadaniom możesz przypisać różne modele.
- **MCP**: aplikacja sama jest serwerem MCP (`/mcp/`, Streamable HTTP). Możesz też podłączyć zewnętrzne serwery MCP (HTTP/SSE), włączać i wyłączać ich narzędzia oraz ustawiać uprawnienia. Niebezpieczne operacje wymagają potwierdzenia w UI.

- **AI TEAM (multi-agent)**: Claude, ChatGPT, Gemini, Ollama i inne modele pracują razem nad jednym projektem jako małe studio. Lead Designer planuje, 3D Engineer buduje geometrię, Texture & Quality Specialist dobiera wygląd. Agenty krytykują się nawzajem, dyskutują, sprawdzają model i poprawiają go. Szczegóły w sekcji [AI TEAM](#ai-team--współpraca-wielu-modeli).

## Stan funkcji

| Obszar | Status |
|---|---|
| Interfejs: projekty, scena (ukrywanie i blokowanie), historia operacji z edycją argumentów, parametry, załączniki, materiały, wersje | ✅ działa |
| Viewport: orbit, zoom, pan, zaznaczanie, manipulatory XYZ (CAD: przesuwanie), siatka, osie, widoki ISO, przód, tył, boki, góra, dół, Wireframe / Solid / Material / Render, HDRI (lokalne studio + presety z CDN), cienie, odbicia, pomiar odległości | ✅ działa |
| CAD: pipeline B-Rep, parametry `=wyrażenie`, edycja istniejącego modelu, eksport STEP/3MF/STL | ✅ działa, pokryte testami |
| GAME: geometria, materiały PBR, tekstury proceduralne, UV (projekcja box, cylindryczna, sferyczna), LOD, optymalizacja, eksport GLB/FBX/OBJ | ✅ działa, pokryte testami |
| Planer offline (bez LLM) | ✅ szablony: obudowa, płyta, tuleja, uchwyt L, bryła wg wymiarów; samochód, postać, dom, drzewo, krzesło, stół, miecz, skrzynia, beczka, teren. Typowe edycje PL/EN. **Nie jest to model AI.** |
| Agent LLM (tool calling) | ✅ zaimplementowany dla 5 typów dostawców. Pętla agenta jest przetestowana na atrapie API zgodnego z OpenAI. **Nie testowano go z prawdziwymi kluczami** (w środowisku budowy nie było kluczy). Sprawdzisz to przyciskiem „Testuj” w Ustawieniach. |
| Analiza zdjęć | ✅ obrazy trafiają do modeli wizyjnych (albo do osobnego modelu „vision”). Planer offline pobiera z nich paletę kolorów. |
| AI TEAM: role, plan, krytyka, debata, decyzje, budowa, kontrola, auto-naprawa, tryby AUTO/REVIEW/MANUAL, limity, failover | ✅ działa. Scenariusze A–F są przetestowane na **emulatorach** API Anthropic, OpenAI i Gemini (prawdziwe formaty przesyłanych danych, odpowiedzi modeli ze skryptu). **Z prawdziwymi modelami nie testowano.** |
| Testy techniczne jakości (QA) | ✅ pomiar kodem: B-Rep, samoprzecięcia, szczelność, non-manifold, grubość ścianek, przenikanie brył, wymiary, stół drukarki, eksport z ponownym importem; w GAME: topologia, poly count, UV, PBR, skala, pivot, LOD |
| Image → 3D (siatka z jednego zdjęcia) | ⏳ **nie zaimplementowane.** Wymaga zewnętrznego generatora (np. Meshy, Tripo, Rodin) albo modelu z GPU (TRELLIS, Hunyuan3D). Architektura narzędzi (`ToolSpec`) jest na to przygotowana. |
| AI Rigging, Animation, Retopology, generator tekstur (dyfuzja), Scene Generator | ⏳ zaplanowane (moduły jako kolejne narzędzia silnika lub adaptery) |
| Hosting | Gotowe konfiguracje (Docker, Render, GitHub Pages, nginx). **Aplikacja nie została opublikowana** (brak danych dostępowych do hostingu). |

## AI TEAM — współpraca wielu modeli

Panel znajdziesz w zakładce **AI Team** po prawej. Połączenia (klucz, adres, model, test, zużycie tokenów i błędy) konfigurujesz w **Ustawienia → AI Connections**. Skład zespołu, role, kolejność, włączanie agentów, ceny i limity ustawiasz pod ikoną ⚙ w panelu AI Team.

**Przebieg zadania** (wszystkie wiadomości to prawdziwe wywołania podłączonych modeli, a backend pośredniczy w całej komunikacji):

```
PLAN (Lead) → niezależne KRYTYKI (pozostali, równolegle, bez wglądu w cudze opinie) → DEBATA (stanowiska agree/disagree
z argumentami, limit rund) → DECYZJA Leada (accept/reject + rozstrzygnięcie konfliktów) → BUDOWA: 3D Engineer (geometria)
→ Quality Specialist (materiały, kolory, logo) → [TESTY TECHNICZNE (kod) → niezależne PRZEGLĄDY → walidacja poprawki przez
silnik → WERYFIKACJA przez innego agenta → zatwierdzenie przez Leada → zapis wersji → wykonanie → ponowne testy] × max rund
→ PODSUMOWANIE
```

**Role zależą od liczby modeli:**

| Liczba modeli | Role |
|---|---|
| 1 | jeden model pełni wszystkie role, a samokontrolę robi w osobnym wywołaniu („świeże oko”) |
| 2 | Lead oraz Engineer + Quality; oba modele sprawdzają się nawzajem |
| 3 | po jednej roli na model |
| więcej | dodatkowe modele dostają rolę Reviewer |

Role ustawione ręcznie mają pierwszeństwo. Gdy agent przestanie odpowiadać (po ponowieniach z backoffem), jego rola automatycznie przechodzi na pozostałych.

**Centralny system operacji.** Schemat przepływu: AGENT → PROJECT CONTEXT → walidacja (narzędzie istnieje, wymagane argumenty, obiekt istnieje, nie jest zablokowany, rola ma uprawnienia do narzędzia) → silnik 3D → historia → viewport.

- Każda operacja ma ID, opis i autora (agenta).
- Każdą operację można cofnąć.
- Quality Specialist nie może dodawać geometrii.

**Kontrola jakości.** Wyniki testów technicznych są liczone kodem i wyraźnie oddzielone od „Ocen AI”, które są opiniami modeli.

- Problem zgłoszony przez AI dostaje oznaczenie „potwierdzony testem” tylko wtedy, gdy wskazuje test, który rzeczywiście zakończył się wynikiem fail/warn. W przeciwnym razie jest oznaczony jako „opinia AI · niezweryfikowane”.
- Testy, których nie przeprowadzono (np. analiza nawisów), są oznaczone jako NIESPRAWDZONE i nie liczą się do wyniku.

**Tryby pracy:**

- **AUTO**: agenty same wprowadzają zatwierdzone poprawki do ustawionego progu ważności.
- **REVIEW**: każda poprawka czeka na Twoje zatwierdzenie lub odrzucenie.
- **MANUAL**: sam wybierasz agenta i zadanie (plan, modelowanie, kontrola, poprawki).

Przed każdą rundą poprawek zapisywana jest wersja projektu. Każdą poprawkę można cofnąć osobno. Panel pokazuje porównanie wyników testów przed i po.

**Limity**: rundy kontroli, rundy debaty, tury narzędzi na agenta, tokeny, czas i koszt (koszt liczony tylko wtedy, gdy podasz cenę za 1M tokenów). Pracę można zatrzymać przyciskiem Stop w dowolnym momencie. Tokeny liczone są z danych `usage` zwracanych przez dostawców.

**Dodanie kolejnego agenta lub roli**: wpis w `ROLES` (`backend/app/team/config.py`). Orkiestrator i UI nie wymagają zmian.

## Architektura

```
frontend/  React 18 + TypeScript + Vite + Tailwind + three.js / React Three Fiber
  ├─ Viewport (GLB per rewizja, animacja nowych elementów, TransformControls, pomiar)
  ├─ LeftPanel (scena, historia, parametry, załączniki, materiały, wersje)
  ├─ AIPanel (dostawca/model, czat, załączniki, Stop/Undo/Redo)
  ├─ LiveBuild (etapy, log operacji, postęp, status WS i silników, Wznów)
  └─ Export / Settings (Modele AI, przypisanie zadań, MCP, połączenie)
          │ REST + WebSocket (/api/ws/{project})
backend/   Python FastAPI
  ├─ engines/cad    CadQuery/OpenCascade: drzewo operacji → B-Rep → teselacja → GLB
  ├─ engines/game   trimesh + manifold3d: scena części, PBR, tekstury proceduralne, UV, LOD
  ├─ runtime.py     historia operacji = źródło prawdy; replay → undo/redo, edycja parametryczna, wersje
  ├─ params.py      parametry użytkownika i bezpieczne wyrażenia „=H-wall”
  ├─ jobs.py        Live Build: kolejne prawdziwe kroki → render → WebSocket; stop, wznowienie, potwierdzenia
  ├─ ai/providers   adaptery: anthropic, openai, openai_compatible, gemini, ollama
  ├─ planner/       planer offline (reguły PL/EN, bez LLM)
  ├─ export/        STL, 3MF (lib3mf), OBJ+MTL, FBX (assimp), GLB/glTF, STEP, PLY, USDZ (usd-core) + walidacja przez ponowny import
  ├─ team/          AI TEAM: config (role, tryby, limity), llm (ponawianie, zużycie, budżet, JSON), context (Project Context),
  │                orchestrator (plan → krytyka → debata → decyzja → budowa → QA/przegląd/poprawki → podsumowanie)
  ├─ qa.py          testy techniczne jakości (CAD + GAME), wyniki 0–100 tylko z pomiarów
  ├─ mcp_server.py  ta aplikacja jako serwer MCP
  └─ mcp_client.py  zewnętrzne serwery MCP (HTTP/SSE)
storage: SQLite (WAL) + pliki w DATA_DIR
```

Wspólny protokół narzędzi (`ToolSpec` z JSON Schema) obsługuje agenta LLM, serwer MCP, planer offline i REST. Dodanie nowej funkcji (np. rigging) oznacza dodanie nowego narzędzia.

Dodanie dostawcy AI: utwórz podklasę `Provider` w `backend/app/ai/providers/adapters.py` (metody `chat()` i `list_models()`) i zarejestruj ją w `REGISTRY`.

## Uruchomienie lokalne

Wymagania: Python 3.11–3.13, Node 20+, opcjonalnie `assimp` (eksport FBX i import FBX/3MF).

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install assimp-utils        # FBX (macOS: brew install assimp)
cp .env.example .env                 # ustaw SECRET_KEY
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload --port 8000

# frontend (drugi terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173 (proxy /api → :8000)
```

Kroki po uruchomieniu:

1. Utwórz projekt i wybierz **CAD** albo **GAME**.
2. Opcjonalnie dodaj dostawcę AI: **Ustawienia → Modele AI**, wklej klucz i kliknij **Testuj**. Bez dostawcy działa planer offline.
3. Wpisz polecenie i obserwuj Live Build.
4. Kliknij **EXPORT MODEL**.

Pełny stack w Dockerze:

```bash
(cd frontend && npm ci && npm run build)
SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))") docker compose up --build
# → http://localhost:8080
```

## Testy

```bash
cd backend && python -m pytest -q tests     # 24 testy
cd frontend && npm run build                # typecheck + build
```

Testy obejmują:

- test CAD ze specyfikacji: obudowa 100×100×30 mm, 4 otwory, logo. Sprawdzają wymiary, poprawność B-Rep, edycję „+5 mm” i kolor, rollback niepoprawnej edycji, eksport STL, 3MF i STEP z ponownym importem;
- test GAME ze specyfikacji: samochód. Sprawdzają etapy, materiały, kolory felg i lakieru, wnętrze, zmianę felg, optymalizację pod Roblox, GLB z normalnymi i teksturami oraz FBX;
- formaty wiadomości wszystkich adapterów;
- API: upload, job, WebSocket, eksport i potwierdzenie usunięcia;
- pętlę agenta LLM na atrapie API zgodnego z OpenAI: etapy, parametry, przekazywanie błędu do modelu i poprawka;
- AI TEAM `tests/test_team.py`, na emulatorach API Anthropic, OpenAI i Gemini:
  - **A**: sam Claude buduje i kontroluje model;
  - **B**: ChatGPT i Gemini sprawdzają się nawzajem;
  - **C**: trzech agentów współpracuje, każdy w swojej roli;
  - **D**: Gemini przestaje odpowiadać, pozostali kończą pracę;
  - **E**: sprzeczne opinie rozstrzyga Lead;
  - **F**: test wykrywa za cienkie logo, w trybie REVIEW następują zatwierdzenie, naprawa, ponowny test i cofnięcie poprawki;
  - dodatkowo tryb MANUAL i limity tokenów.

## Hosting (produkcja)

- **Backend i silnik CAD**: obraz Docker z `backend/Dockerfile`, na dowolnym hoście z ≥2 GB RAM i trwałym dyskiem (Render, Fly.io, Railway, VPS). Blueprint Render jest w `render.yaml`. Ustaw `SECRET_KEY` (wymagany), `CORS_ORIGINS` (URL frontendu) i opcjonalnie `API_TOKEN`.
- **Frontend**: workflow `.github/workflows/pages.yml` publikuje go na GitHub Pages po pushu do `main`. Ustaw zmienną repozytorium `API_URL` (Settings → Variables) na adres backendu i włącz Pages (źródło: GitHub Actions). Frontend możesz też postawić na Vercelu: build `npm run build`, katalog `frontend/dist`, zmienna `VITE_API_URL`.
- **CI**: `.github/workflows/ci.yml` uruchamia testy backendu, build frontendu i build obrazu Docker.
- Za reverse proxy przekaż `/api/` (z WebSocket upgrade) i `/mcp/` do backendu. Przykład jest w `deploy/nginx.conf`.

## MCP: podłączenie agenta do silników

Adres: `https://<backend>/mcp/` (Streamable HTTP). Gdy ustawiony jest `API_TOKEN`, dodaj nagłówek `Authorization: Bearer <API_TOKEN>`.

Narzędzia:

- `list_projects`
- `create_project`
- `list_modeling_tools`
- `get_scene`
- `run_operation`
- `set_parameters`
- `undo`
- `export_model`
- `delete_project` (wymaga zatwierdzenia w otwartym UI)

Zewnętrzne serwery MCP dodajesz w **Ustawienia → Serwery MCP**. Obsługiwane są tylko zdalne serwery HTTP/SSE: uruchamianie procesów `stdio` z sieci byłoby zdalnym wykonaniem kodu.

## Formaty eksportu

| Format | Zawartość | Uwagi |
|---|---|---|
| STL | geometria | brak kolorów i tekstur (ograniczenie formatu) |
| 3MF | geometria + kolory obiektów | lib3mf; części w kolorach dla Bambu, Prusa, Orca |
| OBJ | ZIP: .obj + .mtl + mapy | baseColor, normal, roughness/metal |
| FBX | ZIP z binarnym FBX (tekstury osadzone) | eksport przez assimp; LOD jako `*_LOD0/1/2`; domyślnie cm |
| GLB / glTF | geometria + PBR + tekstury | glTF jako ZIP (.gltf + .bin + obrazy) |
| STEP | dokładne B-Rep (AP214) z nazwami i kolorami | tylko projekty CAD |
| PLY | geometria + kolory wierzchołków | |
| USDZ | USD + UsdPreviewSurface | usd-core; nie testowano na urządzeniu iOS |

Każdy eksport jest ponownie importowany (trimesh, lib3mf, OpenCascade, assimp albo usd-core), a wynik walidacji widać w oknie eksportu.

## Bezpieczeństwo

- Klucze API i nagłówki MCP są szyfrowane (Fernet, klucz z `SECRET_KEY`) i nigdy nie są zwracane w API (pokazujemy tylko maskę).
- Opcjonalny `API_TOKEN` chroni całe API, WebSocket i MCP.
- Upload przyjmuje tylko listę dozwolonych typów, weryfikuje obrazy, odrzuca SVG ze skryptami i ma limit rozmiaru.
- Wyrażenia parametrów są liczone bezpiecznym evaluatorem AST, bez `eval`.
- Usunięcie projektu wymaga jawnego potwierdzenia. Narzędzia MCP z uprawnieniem „pytaj” albo oznaczone jako destrukcyjne wymagają zatwierdzenia w UI.
