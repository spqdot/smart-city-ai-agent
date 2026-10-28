import { FormEvent, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";

const API = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

type Place = {
  name: string; label: string; mode?: string | null; stop_id?: string | null;
  stop_ids?: string[]; stop_ids_by_mode?: Record<string, string[]> | null;
  area?: boolean; lat?: number | null; lon?: number | null;
  municipality?: string | null; area_stop_counts?: Record<string, number> | null;
};
type Leg = { mode: string; vehicle: string; line: string; board: string; alight: string;
  departs: string; arrives?: string | null; board_stop_id?: string; alight_stop_id?: string };
type Journey = { departs: string; arrives?: string | null; duration_minutes?: number | null;
  vehicle: string; line: string; transfers: number; transfer_info: string; legs: Leg[] };
type MapData = { center: [number, number]; bounds: [number, number, number, number];
  paths: { path: [number, number][]; color: number[]; label: string }[];
  stops: { lon: number; lat: number; color: number[]; label: string }[] };
type SearchResult = { options: Journey[]; message?: string; notes?: string[];
  window?: { label?: string }; maps?: MapData[] };

function StopPicker({ label, onPick }: { label: string; onPick: (place: Place | null) => void }) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Place[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Place | null>(null);
  useEffect(() => {
    if (!query.trim() || selected?.name === query) { setSuggestions([]); return; }
    let active = true;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const response = await fetch(`${API}/api/stops?q=${encodeURIComponent(query)}&limit=12`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Stop search is unavailable.");
        if (active) setSuggestions(data.suggestions || []);
      } catch (error) {
        if (active) setSuggestions([]);
      } finally { if (active) setLoading(false); }
    }, 220);
    return () => { active = false; window.clearTimeout(timer); };
  }, [query, selected]);

  function pick(place: Place) {
    setSelected(place); setQuery(place.name); setSuggestions([]); onPick(place);
  }
  return <div className="picker">
    <label>{label}</label>
    <input value={query} placeholder="Start typing a stop or area name" autoComplete="off"
      onChange={(event) => { setQuery(event.target.value); setSelected(null); onPick(null); }} />
    {query.trim() && !selected && <div className="suggestions" role="listbox">
      {loading ? <div className="suggestion-note">Searching stops…</div> : suggestions.length
        ? suggestions.map((place, index) => <button type="button" key={`${place.mode}-${place.stop_id}-${index}`}
            role="option" className="suggestion" onClick={() => pick(place)}>
            <strong>{place.name}</strong><span>{place.label}</span>
          </button>)
        : <div className="suggestion-note">No close stop found yet. Try a shorter spelling or nearby place.</div>}
    </div>}
  </div>;
}

function FitMap({ data }: { data: MapData }) {
  const map = useMap();
  useEffect(() => {
    const [west, south, east, north] = data.bounds;
    map.fitBounds([[south, west], [north, east]], { padding: [24, 24] });
  }, [data, map]);
  return null;
}

function JourneyMap({ data }: { data: MapData }) {
  const center = data.center || [38.72, -9.14];
  return <div className="map-frame"><MapContainer center={center} zoom={11} scrollWheelZoom>
    <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <FitMap data={data} />
    {data.paths.map((path, index) => <Polyline key={index}
      positions={path.path.map(([lon, lat]) => [lat, lon] as [number, number])}
      pathOptions={{ color: `rgb(${path.color.join(",")})`, weight: 5 }}><Tooltip>{path.label}</Tooltip></Polyline>)}
    {data.stops.map((stop, index) => <CircleMarker key={index} center={[stop.lat, stop.lon]}
      radius={8} pathOptions={{ color: "white", weight: 2,
        fillColor: `rgb(${stop.color.join(",")})`, fillOpacity: 1 }}><Tooltip>{stop.label}</Tooltip></CircleMarker>)}
  </MapContainer></div>;
}

export default function App() {
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedJourney, setSelectedJourney] = useState(0);
  const ready = useMemo(() => Boolean(origin && destination), [origin, destination]);

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!origin || !destination) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await fetch(`${API}/api/journeys`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ origin, destination }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Journey search failed.");
      setResult(data); setSelectedJourney(0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Journey search failed.");
    } finally { setBusy(false); }
  }

  const activeJourney = result?.options?.[selectedJourney];
  return <main className="shell">
    <header><div className="eyebrow">LISBON · ALMADA · SETÚBAL</div>
      <h1>Lisbon Journey Planner</h1>
      <p>Choose where you’re starting and going. We’ll find scheduled journeys for the next two hours.</p>
    </header>
    <form onSubmit={search}>
      <section className="pickers">
        <StopPicker label="Where are you going from?" onPick={setOrigin} />
        <StopPicker label="Where do you want to go?" onPick={setDestination} />
      </section>
      <button className="search-button" disabled={!ready || busy}>
        {busy ? "Finding journeys…" : "Show possible journeys"}
      </button>
    </form>
    {error && <div className="notice error">{error}</div>}
    {result && <>
      <section className="results-heading"><div><h2>Possible journeys</h2>
        <p>{result.window?.label || "Next two hours · Lisbon time"}</p></div>
        <span>{result.options.length} found</span></section>
      {!result.options.length && <div className="notice">{result.message || "No scheduled journey was found in the next two hours."}</div>}
      {result.options.length > 0 && <div className="journey-layout">
        <div className="journey-list">{result.options.map((journey, index) => <button
          key={`${journey.departs}-${journey.line}-${index}`}
          className={`journey-card ${index === selectedJourney ? "active" : ""}`}
          onClick={() => setSelectedJourney(index)} type="button">
          <div className="journey-top"><strong>{journey.departs}</strong><span>{journey.vehicle} {journey.line}</span>
            <small>{journey.transfers === 0 ? "Direct" : `${journey.transfers} change${journey.transfers === 1 ? "" : "s"}`}</small></div>
          {journey.legs.map((leg, legIndex) => <div className="leg" key={legIndex}>
            <b>{leg.departs}</b><span>{leg.vehicle} {leg.line}: {leg.board} → {leg.alight}</span>
            <b>{leg.arrives || ""}</b></div>)}
          {journey.transfer_info && journey.transfers > 0 && <p className="transfer">{journey.transfer_info}</p>}
        </button>)}</div>
        {result.maps?.[selectedJourney] && <aside><h2>Route map</h2><JourneyMap data={result.maps[selectedJourney]} />
          <p className="map-caption">Map shows the selected scheduled itinerary and its stops.</p></aside>}
      </div>}
      {result.notes?.map((note, index) => <p className="note" key={index}>{note}</p>)}
    </>}
    <footer>Timetable-based results · Europe/Lisbon · Map tiles © OpenStreetMap contributors</footer>
  </main>;
}
