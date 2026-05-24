from fastapi import FastAPI, Body, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone
import os

app = FastAPI(title="Dann-Alpes API - Reseñas")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Conexión MongoDB ───────────────────────────────────────────────────────────
# En Render: configura MONGO_URI como variable de entorno
# Local: descomenta la línea de abajo y comenta la de os.environ
client = MongoClient(os.environ["MONGO_URI"])
# client = MongoClient("mongodb://TU_USUARIO:TU_PASSWORD@157.253.236.88:8087")

db      = client["ISIS2304H32202610"]   # mismo database del taller
resenas = db["resenas"]                 # nueva colección para esta entrega


# ── Helpers ────────────────────────────────────────────────────────────────────
def ahora() -> datetime:
    """Retorna datetime UTC sin tzinfo (compatible con MongoDB)."""
    return datetime.utcnow()

def parse_fecha(fecha_str: str) -> datetime:
    """Convierte string YYYY-MM-DD a datetime para comparaciones en MongoDB."""
    return datetime.strptime(fecha_str, "%Y-%m-%d")

def parse_fecha_fin(fecha_str: str) -> datetime:
    """Convierte string YYYY-MM-DD al último instante del día."""
    return datetime.strptime(fecha_str + "T23:59:59", "%Y-%m-%dT%H:%M:%S")

def fix_id(doc: dict) -> dict:
    """Convierte ObjectId a string y añade total_votos para JSON."""
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    if doc and "votos_utiles" in doc:
        doc["total_votos"] = len(doc["votos_utiles"])
    # Serializar fechas a ISO string para que FastAPI pueda devolverlas como JSON
    for campo in ["fecha_creacion", "fecha_edicion"]:
        if doc.get(campo) and isinstance(doc[campo], datetime):
            doc[campo] = doc[campo].isoformat()
    if doc.get("respuesta_admin") and isinstance(doc["respuesta_admin"].get("fecha"), datetime):
        doc["respuesta_admin"]["fecha"] = doc["respuesta_admin"]["fecha"].isoformat()
    return doc


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/")
def inicio():
    return {"estado": "API Dann-Alpes funcionando correctamente"}


# ══════════════════════════════════════════════════════════════════════════════
# RF1 – Crear reseña
# POST /hoteles/{hotel_id}/resenas
# Body: { id_usuario, id_reserva, calificacion, texto }
# Regla: una reserva solo puede tener una reseña activa
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/hoteles/{hotel_id}/resenas")
def crear_resena(hotel_id: int, datos: dict = Body(default={})):
    # Verificar que no exista ya una reseña activa para esta reserva
    existente = resenas.find_one({
        "id_reserva": datos.get("id_reserva"),
        "eliminada":  False
    })
    if existente:
        raise HTTPException(
            status_code=400,
            detail="Ya existe una reseña para esta reserva"
        )

    doc = {
        "id_hotel":        hotel_id,
        "id_usuario":      datos.get("id_usuario"),
        "id_reserva":      datos.get("id_reserva"),
        "calificacion":    datos.get("calificacion"),
        "texto":           datos.get("texto"),
        "fecha_creacion":  ahora(),       # ISODate nativo
        "fecha_edicion":   None,
        "destacada":       False,
        "eliminada":       False,
        "votos_utiles":    [],
        "respuesta_admin": None
    }
    result = resenas.insert_one(doc)
    return {"mensaje": "Reseña creada", "id": str(result.inserted_id)}


# ══════════════════════════════════════════════════════════════════════════════
# RF2 – Editar reseña
# PUT /resenas/{resena_id}
# Body: { texto, calificacion }
# ══════════════════════════════════════════════════════════════════════════════
@app.put("/resenas/{resena_id}")
def editar_resena(resena_id: str, datos: dict = Body(default={})):
    result = resenas.update_one(
        {"_id": ObjectId(resena_id), "eliminada": False},
        {"$set": {
            "texto":         datos.get("texto"),
            "calificacion":  datos.get("calificacion"),
            "fecha_edicion": ahora()      # ISODate nativo
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    return {"mensaje": "Reseña actualizada"}


# ══════════════════════════════════════════════════════════════════════════════
# RF3 – Eliminar reseña (cliente)
# DELETE /resenas/{resena_id}/cliente
# Soft delete: marca eliminada=True
# ══════════════════════════════════════════════════════════════════════════════
@app.delete("/resenas/{resena_id}/cliente")
def eliminar_resena_cliente(resena_id: str):
    result = resenas.update_one(
        {"_id": ObjectId(resena_id), "eliminada": False},
        {"$set": {"eliminada": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    return {"mensaje": "Reseña eliminada"}


# ══════════════════════════════════════════════════════════════════════════════
# RF4 – Consultar reseñas de un hotel (público)
# GET /hoteles/{hotel_id}/resenas?orden=fecha|utilidad&pagina=1&por_pagina=10
# Las destacadas siempre aparecen primero
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/hoteles/{hotel_id}/resenas")
def get_resenas_hotel(
    hotel_id:   int,
    orden:      str = Query("fecha", enum=["fecha", "utilidad"]),
    pagina:     int = Query(1, ge=1),
    por_pagina: int = Query(10, ge=1, le=50)
):
    skip  = (pagina - 1) * por_pagina
    match = {"id_hotel": hotel_id, "eliminada": False}

    sort_criterio = (
        {"destacada_orden": 1, "total_votos":    -1}
        if orden == "utilidad"
        else {"destacada_orden": 1, "fecha_creacion": -1}
    )

    pipeline = [
        {"$match": match},
        {"$addFields": {
            "total_votos":     {"$size": "$votos_utiles"},
            "tiene_respuesta": {"$ne": ["$respuesta_admin", None]},
            # destacadas van primero: 0 si destacada, 1 si no
            "destacada_orden": {"$cond": ["$destacada", 0, 1]}
        }},
        {"$sort":  sort_criterio},
        {"$skip":  skip},
        {"$limit": por_pagina}
    ]

    docs = list(resenas.aggregate(pipeline))
    return [fix_id(d) for d in docs]


# ══════════════════════════════════════════════════════════════════════════════
# RF5 – Marcar reseña como útil
# POST /resenas/{resena_id}/votos
# Body: { id_usuario }
# Un usuario solo puede votar una vez por reseña
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/resenas/{resena_id}/votos")
def votar_resena(resena_id: str, datos: dict = Body(default={})):
    id_usuario = datos.get("id_usuario")
    resena = resenas.find_one({"_id": ObjectId(resena_id), "eliminada": False})

    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    if id_usuario in resena.get("votos_utiles", []):
        raise HTTPException(status_code=400, detail="Ya votaste por esta reseña")

    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$push": {"votos_utiles": id_usuario}}
    )
    return {"mensaje": "Voto registrado"}


# ══════════════════════════════════════════════════════════════════════════════
# RF6 – Historial de reseñas propias
# GET /usuarios/{usuario_id}/resenas?orden=fecha|hotel
# Incluye reseñas eliminadas (muestra estado)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/usuarios/{usuario_id}/resenas")
def get_resenas_usuario(
    usuario_id: int,
    orden: str = Query("fecha", enum=["fecha", "hotel"])
):
    pipeline = [
        {"$match": {"id_usuario": usuario_id}},
        {"$addFields": {
            "estado":          {"$cond": ["$eliminada", "eliminada", "publicada"]},
            "total_votos":     {"$size": "$votos_utiles"},
            "tiene_respuesta": {"$ne": ["$respuesta_admin", None]}
        }},
        {"$sort": (
            {"id_hotel": 1}
            if orden == "hotel"
            else {"fecha_creacion": -1}
        )}
    ]

    docs = list(resenas.aggregate(pipeline))
    return [fix_id(d) for d in docs]


# ══════════════════════════════════════════════════════════════════════════════
# RF7 – Responder reseña (admin)
# PUT /resenas/{resena_id}/respuesta
# Body: { texto, id_admin }
# ══════════════════════════════════════════════════════════════════════════════
@app.put("/resenas/{resena_id}/respuesta")
def responder_resena(resena_id: str, datos: dict = Body(default={})):
    result = resenas.update_one(
        {"_id": ObjectId(resena_id), "eliminada": False},
        {"$set": {
            "respuesta_admin": {
                "texto":    datos.get("texto"),
                "fecha":    ahora(),      # ISODate nativo
                "id_admin": datos.get("id_admin")
            }
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    return {"mensaje": "Respuesta guardada"}


# ══════════════════════════════════════════════════════════════════════════════
# RF8 – Eliminar reseña (admin)
# DELETE /resenas/{resena_id}/admin
# Puede eliminar cualquier reseña (activa o no)
# ══════════════════════════════════════════════════════════════════════════════
@app.delete("/resenas/{resena_id}/admin")
def eliminar_resena_admin(resena_id: str):
    result = resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {"eliminada": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    return {"mensaje": "Reseña eliminada por administrador"}


# ══════════════════════════════════════════════════════════════════════════════
# RF9 – Destacar reseña (admin)
# PUT /resenas/{resena_id}/destacar
# Solo puede haber una destacada por hotel — quita la anterior automáticamente
# ══════════════════════════════════════════════════════════════════════════════
@app.put("/resenas/{resena_id}/destacar")
def destacar_resena(resena_id: str):
    resena = resenas.find_one({"_id": ObjectId(resena_id), "eliminada": False})
    if not resena:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")

    # Quitar destacada de todas las reseñas del mismo hotel
    resenas.update_many(
        {"id_hotel": resena["id_hotel"]},
        {"$set": {"destacada": False}}
    )
    # Destacar esta
    resenas.update_one(
        {"_id": ObjectId(resena_id)},
        {"$set": {"destacada": True}}
    )
    return {"mensaje": "Reseña destacada"}


# ══════════════════════════════════════════════════════════════════════════════
# RFC1 – Top 10 hoteles por calificación promedio en un período
# GET /analytics/top-hoteles?fecha_inicio=2026-01-01&fecha_fin=2026-12-31
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/analytics/top-hoteles")
def top_hoteles(
    fecha_inicio: str = Query(None, description="YYYY-MM-DD"),
    fecha_fin:    str = Query(None, description="YYYY-MM-DD")
):
    match = {"eliminada": False}
    if fecha_inicio and fecha_fin:
        match["fecha_creacion"] = {
            "$gte": parse_fecha(fecha_inicio),        # datetime nativo
            "$lte": parse_fecha_fin(fecha_fin)        # datetime nativo al final del día
        }

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":                   "$id_hotel",
            "promedio_calificacion": {"$avg": "$calificacion"},
            "total_resenas":         {"$sum": 1}
        }},
        {"$sort": {"promedio_calificacion": -1}},
        {"$limit": 10},
        {"$project": {
            "id_hotel":              "$_id",
            "promedio_calificacion": {"$round": ["$promedio_calificacion", 2]},
            "total_resenas":         1,
            "_id":                   0
        }}
    ]
    return list(resenas.aggregate(pipeline))


# ══════════════════════════════════════════════════════════════════════════════
# RFC2 – Evolución de la reputación de un hotel mes a mes
# GET /hoteles/{hotel_id}/reputacion?anio=2026
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/hoteles/{hotel_id}/reputacion")
def evolucion_reputacion(hotel_id: int, anio: int = Query(2026)):
    pipeline = [
        {"$match": {
            "id_hotel":  hotel_id,
            "eliminada": False,
            "fecha_creacion": {
                "$gte": datetime(anio, 1, 1),         # ISODate nativo
                "$lte": datetime(anio, 12, 31, 23, 59, 59)  # ISODate nativo
            }
        }},
        {"$addFields": {
            # $month extrae el mes directamente de un campo ISODate
            "mes": {"$month": "$fecha_creacion"}
        }},
        {"$group": {
            "_id":      "$mes",
            "promedio": {"$avg": "$calificacion"},
            "total":    {"$sum": 1}
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "mes":      "$_id",
            "promedio": {"$round": ["$promedio", 2]},
            "total":    1,
            "_id":      0
        }}
    ]
    return list(resenas.aggregate(pipeline))


# ══════════════════════════════════════════════════════════════════════════════
# RFC3 – Comparativa de hoteles por ciudad
# GET /ciudades/comparativa?hotel_ids=1,2,3
# APEX obtiene los IDs de hoteles de una ciudad desde Oracle
# y los pasa aquí como parámetro separado por comas
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/ciudades/comparativa")
def comparativa_ciudad(
    hotel_ids: str = Query(..., description="IDs de hoteles separados por coma. Ej: 1,2,3")
):
    try:
        ids = [int(i.strip()) for i in hotel_ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="hotel_ids debe ser una lista de números separados por coma"
        )

    pipeline = [
        {"$match": {"id_hotel": {"$in": ids}, "eliminada": False}},
        {"$group": {
            "_id":           "$id_hotel",
            "total_resenas": {"$sum": 1},
            "promedio":      {"$avg": "$calificacion"},
            "con_respuesta": {"$sum": {"$cond": [{"$ne": ["$respuesta_admin", None]}, 1, 0]}},
            "destacadas":    {"$sum": {"$cond": ["$destacada", 1, 0]}}
        }},
        {"$project": {
            "id_hotel":              "$_id",
            "total_resenas":         1,
            "promedio_calificacion": {"$round": ["$promedio", 2]},
            "pct_con_respuesta": {
                "$round": [{"$multiply": [
                    {"$cond": [
                        {"$eq": ["$total_resenas", 0]}, 0,
                        {"$divide": ["$con_respuesta", "$total_resenas"]}
                    ]}, 100
                ]}, 1]
            },
            "pct_destacadas": {
                "$round": [{"$multiply": [
                    {"$cond": [
                        {"$eq": ["$total_resenas", 0]}, 0,
                        {"$divide": ["$destacadas", "$total_resenas"]}
                    ]}, 100
                ]}, 1]
            },
            "_id": 0
        }}
    ]
    return list(resenas.aggregate(pipeline))