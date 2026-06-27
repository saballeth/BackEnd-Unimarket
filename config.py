"""
UniMarket - Configuración del sistema
Infraestructura base
Constantes de red 5G Fog-Cloud según especificaciones técnicas
"""

# Constantes MOTOA
NU_THRESHOLD: int   = 3          # ν_threshold  → umbral de cola global
P_CLOUD: float      = 0.05       # Penalización base por offloading a Cloud
EPSILON_TIE: float  = 1e-6       # ε_tie -> desempate numérico

# gNB (Fog Gateway)
GNB_TX_POWER_DBM: float    = 46.0    # Potencia de transmisión (dBm)
GNB_BANDWIDTH_MHZ: float   = 100.0   # Ancho de banda (MHz)
GNB_MIMO_ANTENNAS: int     = 64      # Antenas Massive MIMO
NOISE_FLOOR_DBM: float     = -100.0  # Piso de ruido SINR (dBm)

# Capa Fog / Edge
EDGE_RAM_GBIT: float         = 16.0          # Capacidad RAM (Gbit)
EDGE_CPU_GHZ: float          = 5.0           # Potencia CPU (GHz)
EDGE_BW_MHZ: float           = 500.0         # Ancho de banda (MHz)
EDGE_ACTIVE_POWER_W          = (80.0, 150.0) # Rango potencia activa (Watts)
EDGE_IDLE_POWER_W: float     = 40.0
EDGE_DISTANCE_RANGE_M        = (200.0, 410.0) # Distancia al gNB (m)

# Capa Cloud
CLOUD_RAM_GBIT: float        = 64.0           # Capacidad RAM (Gbit)
CLOUD_CPU_GHZ: float         = 20.0           # Potencia CPU (GHz)
CLOUD_BW_MHZ: float          = 1000.0         # Ancho de banda (MHz)
CLOUD_ACTIVE_POWER_W         = (400.0, 600.0) # Rango potencia activa (Watts)
CLOUD_IDLE_POWER_W: float    = 200.0

# Física de Red 
FIBER_SPEED_MPS: float       = 2e8    # Velocidad de la luz en fibra (m/s)
LIGHT_SPEED_MPS: float       = 3e8    # Velocidad de la luz en aire (m/s)
CLOUD_FIBER_DISTANCE_M: float = 500_000.0  # Distancia estimada al datacenter (m)

# Pesos de optimización por defecto
DEFAULT_ALPHA: float = 0.5   # α → peso objetivo energía
DEFAULT_BETA: float  = 0.5   # β → peso objetivo latencia
