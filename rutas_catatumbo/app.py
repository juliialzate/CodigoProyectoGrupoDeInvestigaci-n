import json
import os

from flask import Flask, jsonify, render_template, request

from algoritmos import Sistema

BASE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE, "templates"))


def cargar():
    with open(os.path.join(BASE, "datos.json"), encoding="utf-8") as f:
        return Sistema(json.load(f))


sistema = cargar()


@app.route("/")
def inicio():
    return render_template("index.html")


@app.route("/api/estado")
def estado():
    return jsonify(sistema.estado())


@app.route("/api/emergencia", methods=["POST"])
def emergencia():
    d = request.get_json()
    try:
        return jsonify(sistema.atender(float(d["lat"]), float(d["lon"]), d["tipo"]))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.route("/api/reiniciar", methods=["POST"])
def reiniciar():
    global sistema
    sistema = cargar()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
