"""
Rutas API: Gestión de tareas y Offloading MOTOA
Motor MOTOA + Backend API
"""
from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from api.schemas import TaskOffloadRequest, TaskOffloadResponse, TaskStatusResponse
from api.deps import get_motoa, get_nodes, get_tasks_db, get_notifier
from models.task import Task, TaskStatus

router = APIRouter(prefix="/tasks", tags=["Tasks & Offloading"])


@router.post(
    "/offload",
    response_model=TaskOffloadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Envía una tarea al motor MOTOA para decisión de offloading",
)
def offload_task(
    payload: TaskOffloadRequest,
    motoa = Depends(get_motoa),
    nodes = Depends(get_nodes),
    tasks_db = Depends(get_tasks_db),
    notifier = Depends(get_notifier),
):
    """
    Pipeline MOTOA:
      1. Validación de plazo D_m
      2. Ajuste dinámico de parámetros
      3. Filtro de viabilidad (RAM + SINR)
      4–5. Normalización + Coste compuesto
      6. Selección de Pareto
      7. Actualización de recursos del nodo
    """
    task = Task(
        user_id = payload.user_id,
        input_size_bits = payload.input_size_bits,
        output_size_bits = payload.output_size_bits,
        uplink_power_dbm = payload.uplink_power_dbm,
        deadline_ms = payload.deadline_ms,
    )

    global_queue = sum(n.queue_length for n in nodes)
    server_id, decision = motoa.process_task(task, nodes, global_queue)

    # Guardar en DB en memoria (MOTOA ya habrá seteado el status)
    tasks_db[task.task_id] = task

    if task.status == TaskStatus.DISCARDED:
        notifier.notify_task_discarded(payload.user_id, "Plazo vencido o SINR insuficiente")
        raise HTTPException(
            status_code = status.HTTP_408_REQUEST_TIMEOUT,
            detail      = {
                "error":    "task_discarded",
                "reason":   "Deadline violation or SINR below threshold",
                "task_id":  task.task_id,
            },
        )

    if task.status == TaskStatus.QUEUED:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail      = {
                "error": "task_queued",
                "reason": "No feasible server with sufficient RAM",
                "task_id": task.task_id,
            },
        )

    task.status = TaskStatus.PROCESSING
    tasks_db[task.task_id] = task
    return TaskOffloadResponse(
        task_id = task.task_id,
        status = task.status.value,
        offloading = decision.value if decision else None,
        server_id = server_id,
        tau_total_ms = task.tau_total_ms,
        energy_total_j = task.energy_total_j,
    )

@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="Consulta el estado y métricas de una tarea",
)
def get_task(
    task_id:  str,
    tasks_db = Depends(get_tasks_db),
):
    task: Task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatusResponse(
        task_id = task.task_id,
        status = task.status.value,
        offloading = task.offloading_decision.value if task.offloading_decision else None,
        tau_uplink_ms = task.tau_uplink_ms,
        tau_downlink_ms = task.tau_downlink_ms,
        tau_comp_ms = task.tau_comp_ms,
        tau_queue_ms = task.tau_queue_ms,
        tau_ram_wait_ms = task.tau_ram_wait_ms,
        tau_total_ms = task.tau_total_ms,
        energy_total_j = task.energy_total_j,
    )
@router.patch(
    "/{task_id}/complete",
    summary="Marca una tarea como completada y libera recursos del nodo",
)
def complete_task(
    task_id:  str,
    tasks_db = Depends(get_tasks_db),
    nodes    = Depends(get_nodes),
):
    task: Task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.PROCESSING:
        raise HTTPException(status_code=409, detail=f"Task status is '{task.status.value}'")

    # Liberar recursos en el nodo asignado
    node = next((n for n in nodes if n.node_id == task.assigned_server_id), None)
    if node:
        ram = task.input_size_bits / 8e9
        node.release(task.task_id, ram)

    task.status = TaskStatus.COMPLETED
    tasks_db[task.task_id] = task
    return {"task_id": task_id, "status": "completed", "freed_ram_gbit": task.input_size_bits / 8e9}


@router.get(
    "/",
    response_model=List[TaskStatusResponse],
    summary="Lista todas las tareas activas",
)
def list_tasks(
    tasks_db = Depends(get_tasks_db),
    status_filter: str = None,
):
    tasks = list(tasks_db.values())
    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter]
    return [
        TaskStatusResponse(
            task_id = t.task_id,
            status = t.status.value,
            offloading = t.offloading_decision.value if t.offloading_decision else None,
            tau_total_ms = t.tau_total_ms,
            energy_total_j = t.energy_total_j,
            tau_uplink_ms = t.tau_uplink_ms,
            tau_downlink_ms = t.tau_downlink_ms,
            tau_comp_ms = t.tau_comp_ms,
            tau_queue_ms = t.tau_queue_ms,
            tau_ram_wait_ms = t.tau_ram_wait_ms,
        )
        for t in tasks
    ]
