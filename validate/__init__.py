"""Validation gates (P1 §4.4). Where each gate lives:

L0 harness/lint.py · L1, L2, L3-local harness/part_ops.py (worker) · L3 plan-side harness/tools.py
plan validator (tree, DOF, refs, tool, reach, L6-plan) validate/plan_check.py
L5 interference + reach, L6 torque validate/assembly_check.py (worker op sweep_assembly: validate/sweep.py)
L7-basic validate/printability.py (meshes from worker op mesh_parts: validate/sweep.py)
"""
