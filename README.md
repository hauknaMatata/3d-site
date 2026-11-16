# AI 3D Studio

Aplikacja webowa do tworzenia, edycji i eksportu modeli 3D z pomocą AI. Model powstaje na żywo w prawdziwym silniku 3D, a nie w atrapie.

- **CAD Mode**: precyzyjne bryły B-Rep na **OpenCascade (CadQuery)**. Wymiary w mm, parametry użytkownika, szkice, extrude, revolve, boolean, fillet, chamfer, shell, hole, pattern, mirror, pomiary, logo wytłaczane lub grawerowane z obrazu. Eksport STEP, 3MF (z kolorami), STL i innych formatów.
- **Game Mode**: kolorowe assety PBR (trimesh + manifold3d). Loft, lathe, tube i prymitywy z UV, złożone komponenty (koło, postać, drzewo), proceduralne mapy PBR (albedo, normal, roughness, metallic, AO), naklejki rzutowane na powierzchnię, LOD, decymacja, optymalizacja pod Roblox, Unity, Unreal i web. Eksport GLB, FBX, OBJ, glTF, USDZ.
- **Live Build**: każdy krok to prawdziwa operacja silnika. Po każdym kroku backend renderuje nowy GLB i wysyła go przez **WebSocket**. Nowe elementy pojawiają się w viewporcie z animacją. Masz etapy, log, Stop, Undo/Redo i wznowienie zadania po błędzie.
- **AI Provider Manager**: Anthropic Claude, OpenAI, Google Gemini, Ollama i dowolne API zgodne z OpenAI, przez bezpośrednie REST API, niezależnie od MCP. Klucze są szyfrowane na serwerze i nigdy nie trafiają do przeglądarki. Różnym zadaniom możesz przypisać różne modele.
- **MCP**: aplikacja sama jest serwerem MCP (`/mcp/`, Streamable HTTP). Możesz też podłączyć zewnętrzne serwery MCP (HTTP/SSE), włączać i wyłączać ich narzędzia oraz ustawiać uprawnienia. Niebezpieczne operacje wymagają potwierdzenia w UI.

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
| Image → 3D (siatka z jednego zdjęcia) | ⏳ **nie zaimplementowane.** Wymaga zewnętrznego generatora (np. Meshy, Tripo, Rodin) albo modelu z GPU (TRELLIS, Hunyuan3D). Architektura narzędzi (`ToolSpec`) jest na to przygotowana. |
| AI Rigging, Animation, Retopology, generator tekstur (dyfuzja), Scene Generator | ⏳ zaplanowane (moduły jako kolejne narzędzia silnika lub adaptery) |
| Hosting | Gotowe konfiguracje (Docker, Render, GitHub Pages, nginx). **Aplikacja nie została opublikowana** (brak danych dostępowych do hostingu). |

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
cd backend && python -m pytest -q tests     # 16 testów
cd frontend && npm run build                # typecheck + build
```

Testy obejmują:

- test CAD ze specyfikacji: obudowa 100×100×30 mm, 4 otwory, logo. Sprawdzają wymiary, poprawność B-Rep, edycję „+5 mm” i kolor, rollback niepoprawnej edycji, eksport STL, 3MF i STEP z ponownym importem;
- test GAME ze specyfikacji: samochód. Sprawdzają etapy, materiały, kolory felg i lakieru, wnętrze, zmianę felg, optymalizację pod Roblox, GLB z normalnymi i teksturami oraz FBX;
- formaty wiadomości wszystkich adapterów;
- API: upload, job, WebSocket, eksport i potwierdzenie usunięcia;
- pętlę agenta LLM na atrapie API zgodnego z OpenAI: etapy, parametry, przekazywanie błędu do modelu i poprawka.

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
