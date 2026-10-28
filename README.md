# Lisbon Journey Planner

Journey search stays in the existing Python/LangGraph service. The new split deployment uses a FastAPI backend on Render and a React/Vite dashboard on Vercel. The old Streamlit dashboard remains available for local use.

## Run locally

From the repository root, install the Python requirements and start the API:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite proxies `/api` requests to `http://127.0.0.1:8000`. The journey API uses the same deterministic search service and local timetables as the Streamlit planner.

## Data required by the API

Transport data is deliberately excluded from Git. The local `data/` directory is about 1.64 GB and contains raw GTFS input that should not be committed. The API needs a smaller runtime bundle containing the bus timetable index, stop and graph files, each `data/modes/<mode>/` directory, and the MST schedule snapshot.

Create that bundle locally:

```powershell
python scripts/package_runtime_data.py
```

The script creates `runtime-data.zip` at the repository root. Upload it as a release asset at a URL that Render can access over HTTPS. Do not put the archive in the Git repository. Make sure you have permission to redistribute the timetable files before making an asset public. Regenerate the bundle when you update the operator feeds.

If the runtime bundle is absent, `/api/health` still reports that the process is alive, while stop and journey requests return a clear data-unavailable error.

## GitHub repository

GitHub calls this a **repository**, not a folder. This local Git repository currently has no commits and no GitHub remote. Create an empty repository on GitHub (for example, `lisbon-journey-planner`); do not initialize it with a README because this project already has one. Then, from this directory:

```powershell
git add .
git commit -m "Prepare Lisbon journey planner for web deployment"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/lisbon-journey-planner.git
git push -u origin main
```

The `.gitignore` keeps `.env`, Python environments, Node build output, pytest scratch files, and all of `data/` out of Git. Never commit API keys.

## Deploy the backend to Render

The root `render.yaml` defines the FastAPI web service and installs only its lean Python dependencies. In Render, choose **New → Blueprint** and connect the GitHub repository. Before deploying, configure these service environment variables:

- `SMARTCITY_DATA_ARCHIVE_URL`: HTTPS download URL for `runtime-data.zip`.
- `SMARTCITY_DATA_ARCHIVE_SHA256`: optional SHA-256 checksum of that ZIP.
- `CORS_ORIGINS`: the Vercel production origin, such as `https://lisbon-journey-planner.vercel.app` (no trailing slash).

Render's build command downloads and installs the bundle into the deployed image, then starts `uvicorn api.main:app`. The `/api/health` route checks process health; use `/docs` to inspect API routes. The free Render web service sleeps when idle and has an ephemeral filesystem, so data is installed during each build instead of being written at runtime. A sleeping free service can take about a minute to wake up.

## Deploy the frontend to Vercel

Import the same GitHub repository as a Vercel project and set **Root Directory** to `frontend`. Vercel detects Vite; use `npm run build` and `dist` if it asks for the build command or output directory. Set the production environment variable:

```text
VITE_API_URL=https://YOUR-RENDER-SERVICE.onrender.com
```

After the frontend deploys, copy its production origin into Render's `CORS_ORIGINS`, then redeploy the backend. The map uses public OpenStreetMap tiles.

## Existing local Streamlit dashboard

```powershell
streamlit run app.py
```
