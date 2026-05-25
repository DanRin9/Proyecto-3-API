import os
os.environ["MONGO_URI"] = "mongodb://ISIS2304H32202610:IAeFJw2NKaXC@157.253.236.88:8087"

from pymongo import MongoClient
from datetime import datetime
from pprint import pprint

client  = MongoClient(os.environ["MONGO_URI"])
db      = client["ISIS2304H32202610"]
resenas = db["resenas"]

print("\n========== RFC1: Top 10 hoteles ==========")
pipeline_rfc1 = [
    {"$match": {
        "eliminada": False,
        "fecha_creacion": {
            "$gte": datetime(2026, 1, 1),
            "$lte": datetime(2026, 12, 31, 23, 59, 59)
        }
    }},
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
pprint(list(resenas.aggregate(pipeline_rfc1)))

print("\n========== RFC2: Evolución hotel 1, año 2026 ==========")
pipeline_rfc2 = [
    {"$match": {
        "id_hotel":  1,
        "eliminada": False,
        "fecha_creacion": {
            "$gte": datetime(2026, 1, 1),
            "$lte": datetime(2026, 12, 31, 23, 59, 59)
        }
    }},
    {"$addFields": {
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
pprint(list(resenas.aggregate(pipeline_rfc2)))

print("\n========== RFC3: Comparativa ciudad (hoteles 1,2,3) ==========")
pipeline_rfc3 = [
    {"$match": {"id_hotel": {"$in": [1, 2, 3]}, "eliminada": False}},
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
pprint(list(resenas.aggregate(pipeline_rfc3)))