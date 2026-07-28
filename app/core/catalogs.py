# app/core/catalogs.py
from enum import Enum
from typing import Set

# Catálogo oficial derivado da tua resposta JSON
VALID_ENERGY_CLASSES: Set[str] = {
    "A", "A+", "B", "B-", "C", "D", "E", 
    "Evaluation in progress", "Exempted", "F", "G", "Unavailable"
}

# Subconjunto útil para regras de negócio:
EXEMPT_ENERGY_CLASSES: Set[str] = {
    "Evaluation in progress", "Exempted", "Unavailable"
}

VALID_PROPERTY_TYPES: Set[str] = {
    "Aparthotel", "Apartment", "Archive Agency", "Article", "Atelier", "Atl", 
    "Bakery", "Bar", "Bar / Restaurant", "Beauty Institute", "Block of Flats", 
    "Boarding House", "Building", "Building Lot", "Bungalow", "Café / Snack Bar", 
    "Car Stand", "Carpentry", "Clinic", "College", "Commercial Store", 
    "Commercial Store (Ground Floor)", "Country House", "Detached House", 
    "Development", "Disco", "Drugstore", "Duplex", "Externato", "Factory", 
    "Farm", "Farm / Homestead", "Fishmonger", "Florist", "Fruit Shop", "Garage", 
    "Golf Field", "Guest House", "Gym", "Hair Stylist / Beauty Center", 
    "Homestead", "Hostel", "Hotel", "House", "House Loft", "House Lot", 
    "Houses Allotment", "Ice Cream Shop", "Industrial", "Industrial Ground", 
    "Industrial Lot", "Inn", "Jewelery / Watches", "Kindergarten", "Laundry", 
    "Lot", "Mansion", "Mini Market", "Mixed Ground", "Motel", "Multipurpose Space", 
    "N/A", "Newsstand", "Nursery", "Nursing Home", "Office", "Office Building", 
    "Old House", "Parking Lot", "Pavilion", "Penthouse", "Perfumery", "Pharmacy", 
    "Ready-to-wear Shop", "Restaurant", "Rural Farm", "Rustic Ground", "Shipyard", 
    "Small Villa", "Spa", "Storage House", "Store", "Studio", "Townhouse", 
    "Training Center", "Travel Agency", "Turismo em Espaço Rural", 
    "Two-family House", "Units", "Urban Ground", "Villa", "Village", 
    "Warehouse", "Wood House", "Workshop"
}

LAND_PROPERTY_TYPES: Set[str] = {
    "Building Lot", "House Lot", "Houses Allotment", "Industrial Ground", 
    "Industrial Lot", "Lot", "Mixed Ground", "Rustic Ground", "Urban Ground"
}

# (Podes adicionar os restantes conjuntos para state, business_type, etc.)