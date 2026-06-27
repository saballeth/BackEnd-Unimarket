"""
Recomendaciones personalizadas
Lógica de notificaciones y recomendaciones

Aplica dominancia de Pareto para filtrar y rankear productos,
balanceando relevancia, precio y latencia de carga.
Evita el sesgo hacia productos regulares populares permitiendo
que productos nuevos escalen si no penalizan la latencia
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import EPSILON_TIE
from models.product import Product, ProductStatus
from models.user import UserProfile

logger = logging.getLogger(__name__)


@dataclass
class RankedProduct:
    product: Product
    relevance_score: float
    pareto_rank: int
    is_pareto_front: bool

    def to_dict(self) -> dict:
        p = self.product
        return {
            "product_id": p.product_id,
            "name": p.name,
            "category": p.category,
            "price": p.price,
            "stock": p.stock,
            "status": p.status.value,
            "relevance": round(self.relevance_score, 4),
            "pareto_rank": self.pareto_rank,
            "pareto_front": self.is_pareto_front,
        }

class RecommendationService:
    """
    Motor de recomendaciones basado en dominancia de Pareto.

    Criterio:
      Recomendación A domina a B si A no es inferior en ningún atributo
      (relevancia, precio, latencia) y es estrictamente superior en al menos uno.

    Estrategia de visibilidad:
      Productos NUEVOS con alta relevancia escalan posiciones si no penalizan
      la latencia del sistema frente a productos REGULARES establecidos.
    """

    # Ratio de intercalado Nuevos:Regulares en la lista final
    NEW_INTERLEAVE_RATIO: float = 0.4   # 40% de slots reservados a productos Nuevos

    def get_recommendations(
        self,
        user: Optional[UserProfile],
        products: List[Product],
        max_results: int = 10,
    ) -> List[RankedProduct]:
        """
        Pipeline completo:
          1. Calcular score de relevancia por usuario
          2. Construir frente de Pareto
          3. Rankear con equilibrio Nuevo/Regular
        """
        if not products:
            return []
        # Paso 1: Score de relevancia
        scored = self._score_relevance(products, user)

        # Paso 2: Frente de Pareto
        ranked = self._build_pareto_ranking(scored)

        # Paso 3: Intercalado Nuevo / Regular para visibilidad
        final = self._interleave_visibility(ranked)

        logger.info(
            "Recomendaciones: %d productos → %d en frente Pareto → top %d",
            len(products),
            sum(1 for r in ranked if r.is_pareto_front),
            min(max_results, len(final)),
        )
        return final[:max_results]

    # Paso 1 – Relevancia
    def _score_relevance(
        self,
        products: List[Product],
        user: Optional[UserProfile],
    ) -> List[Product]:
        """
        Calcula relevance_score para cada producto según las preferencias del usuario.
        Productos NUEVOS reciben un boost base de visibilidad
        """
        for p in products:
            base = 0.5

            if user and p.category in user.category_preferences:
                base += user.category_preferences[p.category] * 0.4

            # Boost de visibilidad para productos nuevos
            if p.status == ProductStatus.NEW:
                base += 0.10

            # Penalización leve por stock bajo
            if p.stock == 0:
                base -= 0.30
            elif p.stock < 5:
                base -= 0.10

            p.relevance_score = max(0.0, min(1.0, base))

            # Latencia de carga simulada (inversamente proporcional a relevancia)
            p.load_latency_ms = max(5.0, 100.0 - p.relevance_score * 80.0)

        return products
    # Paso 2 – Dominancia de Pareto
    def _build_pareto_ranking(self, products: List[Product]) -> List[RankedProduct]:
        """
        Construye el ranking por capas de Pareto (non-dominated sorting).
        Atributos evaluados:
          - relevance_score → maximizar
          - price → minimizar (se invierte el signo)
          - load_latency_ms → minimizar (se invierte el signo)
        """
        # Convertimos a minimización: todos los objetivos se minimizan
        def objectives(p: Product) -> Dict[str, float]:
            return {
                "neg_relevance": -p.relevance_score,    # minimizar negativo
                "price":          p.price,              # minimizar
                "latency":        p.load_latency_ms,    # minimizar
            }
        remaining = list(products)
        ranked = []
        pareto_rank = 1

        while remaining:
            front = self._pareto_front(remaining, objectives)
            for p in front:
                is_front = (pareto_rank == 1)
                ranked.append(RankedProduct(
                    product        = p,
                    relevance_score= p.relevance_score,
                    pareto_rank    = pareto_rank,
                    is_pareto_front= is_front,
                ))
            remaining   = [p for p in remaining if p not in front]
            pareto_rank += 1

        return ranked

    def _pareto_front(
        self,
        products: List[Product],
        objectives,
    ) -> List[Product]:
        """
        Retorna los productos no dominados dentro del conjunto.
        A domina B si A ≤ B en todos los objetivos y A < B en al menos uno.
        """
        front = []
        for a in products:
            dominated = False
            obj_a = objectives(a)
            for b in products:
                if b is a:
                    continue
                obj_b = objectives(b)
                keys = list(obj_a.keys())

                b_not_worse = all(
                    obj_b[k] <= obj_a[k] + EPSILON_TIE for k in keys
                )
                b_strictly  = any(
                    obj_b[k] <  obj_a[k] - EPSILON_TIE for k in keys
                )
                if b_not_worse and b_strictly:
                    dominated = True
                    break

            if not dominated:
                front.append(a)

        return front

    # Paso 3 – Intercalado de Visibilidad (Nuevo vs Regular)
    def _interleave_visibility(self, ranked: List[RankedProduct]) -> List[RankedProduct]:
        """
        Intercala productos NUEVOS y REGULARES para evitar el sesgo
        hacia populares. Aplica el ratio NEW_INTERLEAVE_RATIO.

        Ejemplo con ratio=0.4 y 10 slots: 4 Nuevos + 6 Regulares,
        intercalados posicionalmente.
        """
        news = [r for r in ranked if r.product.status == ProductStatus.NEW]
        regulars = [r for r in ranked if r.product.status == ProductStatus.REGULAR]

        result = []
        ni, ri = 0, 0
        total = len(news) + len(regulars)

        for slot in range(total):
            use_new = (
                ni < len(news) and
                (ri >= len(regulars) or slot / total < self.NEW_INTERLEAVE_RATIO * (ni + 1) / (ni + 1))
            )
            if use_new:
                result.append(news[ni]); ni += 1
            elif ri < len(regulars):
                result.append(regulars[ri]); ri += 1
            elif ni < len(news):
                result.append(news[ni]); ni += 1

        return result

    # Búsqueda con filtro Tabú (motor de búsqueda)
    def search(
        self,
        products: List[Product],
        query: str,
        category: Optional[str],
        user: Optional[UserProfile],
        max_results: int = 10,
        tabu_list: Optional[List[str]] = None,
    ) -> List[RankedProduct]:
        """
        Búsqueda Tabú: filtra productos ya vistos (tabu_list) para garantizar
        diversidad de resultados y evitar mostrar siempre los mismos
        """
        tabu = set(tabu_list or [])
        q = query.lower().strip()

        candidates = [
            p for p in products
            if p.product_id not in tabu
            and p.stock > 0
            and (not q or q in p.name.lower() or q in (p.description or "").lower())
            and (not category or p.category.lower() == category.lower())
        ]

        return self.get_recommendations(user, candidates, max_results)
