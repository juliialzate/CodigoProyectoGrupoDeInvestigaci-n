"""Grafo principal + Prim + A* para rutas de ayuda humanitaria (Tibú, El Tarra, Teorama).

GRAFO PRINCIPAL: nodos = estaciones de policía, hospitales, alcaldías, centros de ayuda y poblados.
Cada nodo se conecta a sus 3 vecinos más cercanos; el peso de cada arista = tiempo de recorrido (s).
  - Si se descargaron las calles de OpenStreetMap, el tiempo y el trazado siguen las calles (30 km/h).
  - Si no hay calles disponibles, se usa línea recta x 1.3 (urbano) o x 1.4 a 40 km/h (entre municipios).
Los puntos que el usuario marca en el mapa se AÑADEN a este grafo y se conectan a sus 3 vecinos más cercanos.
"""
import heapq
import itertools
import json
import math
import os
import time

import networkx as nx
import requests

V_URBANA = 30 / 3.6   # m/s
V_MAX = 40 / 3.6      # m/s (heurística de A*: nunca se supera, así es admisible)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
K_VECINOS = 3
_desempate = itertools.count()

# tipo de transporte -> (nodo estratégico desde donde sale, tipo de destino posterior)
TRANSPORTES = {
    "ambulancia": ("hospital", "hospital"),
    "policia": ("policia", "policia"),
    "carga": ("alcaldia", "centro_ayuda"),
}


def haversine(lat1, lon1, lat2, lon2):
    """Distancia en metros entre dos coordenadas."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ------------------------------------------- calles de OpenStreetMap (opcionales)
def descargar_calles(nombre, lat, lon, radio=2500):
    ruta = os.path.join(CACHE, f"{nombre}.json")
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    consulta = f"""[out:json][timeout:60];
    way["highway"~"^(trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|track)$"]
    (around:{radio},{lat},{lon});(._;>;);out body;"""
    cabeceras = {"User-Agent": "rutas-catatumbo-investigacion/1.0 (proyecto universitario)"}
    error = None
    for intento in range(4):
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": consulta}, headers=cabeceras, timeout=90)
                r.raise_for_status()
                datos = r.json()
                os.makedirs(CACHE, exist_ok=True)
                with open(ruta, "w", encoding="utf-8") as f:
                    json.dump(datos, f)
                time.sleep(5)          # pausa para no saturar el servidor en el siguiente municipio
                return datos
            except Exception as ex:
                error = ex
        print(f"[...] {nombre}: el servidor pide esperar, reintento {intento + 1}/4 en {15 * (intento + 1)} s")
        time.sleep(15 * (intento + 1))
    raise error


def agregar_calles(H, datos):
    nodos = {e["id"]: (e["lat"], e["lon"]) for e in datos["elements"] if e["type"] == "node"}
    for e in datos["elements"]:
        if e["type"] != "way":
            continue
        for a, b in zip(e["nodes"], e["nodes"][1:]):
            if a in nodos and b in nodos:
                d = haversine(*nodos[a], *nodos[b])
                H.add_node(a, lat=nodos[a][0], lon=nodos[a][1])
                H.add_node(b, lat=nodos[b][0], lon=nodos[b][1])
                H.add_edge(a, b, w=d / V_URBANA)


def construir_calles(cfg):
    S = nx.Graph()
    for nombre, (lat, lon) in cfg["municipios"].items():
        H = nx.Graph()
        try:
            agregar_calles(H, descargar_calles(nombre, lat, lon))
        except Exception as ex:
            print(f"[!] Sin calles de {nombre} ({ex}). Se usarán líneas rectas ahí.")
        if len(H):
            S = nx.compose(S, H.subgraph(max(nx.connected_components(H), key=len)))
    S.graph["nodos"] = list(S.nodes)
    return S


# ---------------------------------------------------------------- algoritmos
def prim(G):
    """Árbol de expansión mínima del grafo principal: [(u, v, peso)]."""
    inicio = next(iter(G.nodes))
    visitados = {inicio}
    cola = [(e["w"], next(_desempate), inicio, v) for v, e in G[inicio].items()]
    heapq.heapify(cola)
    res = []
    while cola and len(visitados) < len(G):
        w, _, u, v = heapq.heappop(cola)
        if v in visitados:
            continue
        visitados.add(v)
        res.append((u, v, w))
        for x, e in G[v].items():
            if x not in visitados:
                heapq.heappush(cola, (e["w"], next(_desempate), v, x))
    return res


def a_estrella(G, inicio, meta):
    """Ruta más rápida en el grafo principal. Devuelve (segundos, [nodos]).
    Los puntos de desplazamiento no se usan como paso intermedio, solo como inicio o meta."""
    lat_t, lon_t = G.nodes[meta]["lat"], G.nodes[meta]["lon"]

    def h(n):
        return haversine(G.nodes[n]["lat"], G.nodes[n]["lon"], lat_t, lon_t) / V_MAX

    g = {inicio: 0}
    previo = {}
    cola = [(h(inicio), next(_desempate), inicio)]
    cerrados = set()
    while cola:
        _, _, u = heapq.heappop(cola)
        if u == meta:
            break
        if u in cerrados:
            continue
        cerrados.add(u)
        for v, e in G[u].items():
            if v != meta and G.nodes[v]["tipo"] == "desplazamiento":
                continue
            nuevo = g[u] + e["w"]
            if nuevo < g.get(v, math.inf):
                g[v] = nuevo
                previo[v] = u
                heapq.heappush(cola, (nuevo + h(v), next(_desempate), v))
    if meta not in g:
        return math.inf, []
    camino = [meta]
    while camino[-1] != inicio:
        camino.append(previo[camino[-1]])
    return g[meta], camino[::-1]


# ------------------------------------------------------------------- sistema
class Sistema:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calles = construir_calles(cfg)      # solo para dar forma/peso a las aristas
        self._snap = {}
        self.n_usuarios = 0
        self.G = nx.Graph()                      # GRAFO PRINCIPAL
        for p in cfg["puntos"]:
            self.G.add_node("p_" + p["id"], nombre=p["nombre"], municipio=p["municipio"], tipo=p["tipo"],
                            direccion=p.get("direccion", ""), lat=p["lat"], lon=p["lon"])
        nodos = list(self.G.nodes)
        for n in nodos:
            for v in self._vecinos(n):
                self._agregar_arista(n, v)
        self._unir_componentes()
        self.mst = prim(self.G)

    # --- construcción de aristas
    def _vecinos(self, nid, k=K_VECINOS):
        """Los k nodos fijos (no desplazamientos) más cercanos."""
        A = self.G.nodes[nid]
        otros = [n for n in self.G.nodes if n != nid and self.G.nodes[n]["tipo"] != "desplazamiento"]
        otros.sort(key=lambda n: haversine(A["lat"], A["lon"], self.G.nodes[n]["lat"], self.G.nodes[n]["lon"]))
        return otros[:k]

    def _calle_cercana(self, nid):
        if nid not in self._snap:
            A = self.G.nodes[nid]
            mejor = min(self.calles.graph["nodos"],
                        key=lambda n: haversine(A["lat"], A["lon"], self.calles.nodes[n]["lat"], self.calles.nodes[n]["lon"]))
            d = haversine(A["lat"], A["lon"], self.calles.nodes[mejor]["lat"], self.calles.nodes[mejor]["lon"])
            self._snap[nid] = (mejor, d)
        return self._snap[nid]

    def _enlace(self, a, b):
        """(tiempo en s, trazado) entre dos nodos del grafo principal."""
        A, B = self.G.nodes[a], self.G.nodes[b]
        pa, pb = [A["lat"], A["lon"]], [B["lat"], B["lon"]]
        d = haversine(*pa, *pb)
        if d > 5000:                                   # carretera entre municipios (aproximada)
            return d * 1.4 / V_MAX, [pa, pb]
        if len(self.calles):
            (na, da), (nb, db) = self._calle_cercana(a), self._calle_cercana(b)
            if da < 400 and db < 400:
                try:
                    t, camino = nx.single_source_dijkstra(self.calles, na, nb, weight="w")
                    linea = [pa] + [[self.calles.nodes[n]["lat"], self.calles.nodes[n]["lon"]] for n in camino] + [pb]
                    return t + (da + db) / V_URBANA, linea
                except nx.NetworkXNoPath:
                    pass
        return d * 1.3 / V_URBANA, [pa, pb]

    def _agregar_arista(self, a, b):
        if a == b or self.G.has_edge(a, b):
            return
        t, linea = self._enlace(a, b)
        self.G.add_edge(a, b, w=max(t, 1), linea=linea, a=a)

    def _unir_componentes(self):
        """Garantiza que el grafo sea conexo (une los municipios por su par de nodos más cercano)."""
        while True:
            comps = list(nx.connected_components(self.G))
            if len(comps) == 1:
                return
            base, resto = comps[0], set().union(*comps[1:])
            a, b = min(((x, y) for x in base for y in resto),
                       key=lambda p: haversine(self.G.nodes[p[0]]["lat"], self.G.nodes[p[0]]["lon"],
                                               self.G.nodes[p[1]]["lat"], self.G.nodes[p[1]]["lon"]))
            self._agregar_arista(a, b)

    # --- consultas
    def de_tipo(self, tipo):
        return [n for n in self.G.nodes if self.G.nodes[n]["tipo"] == tipo]

    def estrategicos(self):
        """Nodos del árbol de Prim con grado >= 2 (los más 'centrales' de la red).
        Ahí se ubican ambulancias (hospitales), patrullas (policía) y transporte de carga (alcaldías)."""
        grado = {}
        for u, v, _ in self.mst:
            grado[u] = grado.get(u, 0) + 1
            grado[v] = grado.get(v, 0) + 1
        res = {}
        for tipo in ("hospital", "policia", "alcaldia"):
            todos = self.de_tipo(tipo)
            res[tipo] = [n for n in todos if grado.get(n, 0) >= 2] or todos
        return res

    def trazado(self, camino):
        pts = []
        for u, v in zip(camino, camino[1:]):
            e = self.G[u][v]
            pts += e["linea"] if e["a"] == u else e["linea"][::-1]
        return pts

    def estado(self):
        en_mst = {frozenset((u, v)) for u, v, _ in self.mst}
        return {
            "pois": [{"id": n, **d} for n, d in self.G.nodes(data=True)],
            "aristas": [{"a": u, "b": v, "min": round(e["w"] / 60, 1), "linea": e["linea"],
                         "mst": frozenset((u, v)) in en_mst} for u, v, e in self.G.edges(data=True)],
            "estrategicos": self.estrategicos(),
            "total_min": round(sum(w for *_, w in self.mst) / 60, 1),
            "con_calles": len(self.calles) > 0,
        }

    def atender(self, lat, lon, tipo):
        if tipo not in TRANSPORTES:
            raise ValueError("Tipo de transporte no válido.")
        if min(haversine(lat, lon, *c) for c in self.cfg["municipios"].values()) > 3500:
            raise ValueError("El punto está fuera de Tibú, El Tarra o Teorama.")
        # 1. el punto se AÑADE al grafo principal y se conecta a sus vecinos más cercanos
        self.n_usuarios += 1
        nid = f"u_{self.n_usuarios}"
        self.G.add_node(nid, nombre=f"Punto de desplazamiento #{self.n_usuarios}", municipio="",
                        tipo="desplazamiento", direccion="", lat=lat, lon=lon)
        for v in self._vecinos(nid):
            self._agregar_arista(nid, v)
        self.mst = prim(self.G)                      # 2. Prim lo incluye
        est = self.estrategicos()
        tipo_origen, tipo_destino = TRANSPORTES[tipo]

        # 3. A*: transporte estratégico que llega más rápido
        t, camino, o = min(((*a_estrella(self.G, o, nid), o) for o in est[tipo_origen]), key=lambda x: x[0])
        nombre = lambda n: self.G.nodes[n]["nombre"]
        res = {"usuario": {"id": nid, "nombre": self.G.nodes[nid]["nombre"]}, "tipo": tipo,
               "origen": nombre(o), "origen_direccion": self.G.nodes[o]["direccion"],
               "ida": self.trazado(camino), "min_ida": round(t / 60, 1), "via": [nombre(n) for n in camino]}

        # 4. A*: destino posterior más rápido (hospital, estación de policía o centro de ayuda)
        t, camino, d = min(((*a_estrella(self.G, nid, d), d) for d in self.de_tipo(tipo_destino)), key=lambda x: x[0])
        res.update(destino=nombre(d), destino_direccion=self.G.nodes[d]["direccion"],
                   vuelta=self.trazado(camino), min_vuelta=round(t / 60, 1))
        return res
