"""Structured-output schemas the factory's LLM calls must conform to."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Binding(BaseModel):
    param: str = Field(description="input field name of the step")
    source: str = Field(description="'$input.<field>' | '$steps.<step_id>.<field>' | '$steps.<step_id>' | JSON literal")


class StepSpec(BaseModel):
    id: str = Field(description="short step id, e.g. s1")
    uses: str = Field(description="name of an existing active capability OR the name of a gap below")
    foreach: Optional[str] = Field(default=None, description="optional binding to a LIST; the step then runs once per "
                                   "element (bind it as '$item' or '$item.<field>') and its output is {'items': [...]}. "
                                   "Use this to reuse single-item capabilities on lists instead of building batch versions.")
    inputs: list[Binding]


class GapSpec(BaseModel):
    name: str = Field(description="snake_case capability name, generic and reusable (not task-specific)")
    description: str
    why_missing: str = Field(description="what in the registry falls short")
    kind: Literal["code", "llm"] = Field(description="code unless the step truly needs language judgment")
    input_schema_json: str = Field(description="JSON Schema (object) of the input dict, as a JSON string")
    output_schema_json: str = Field(description="JSON Schema (object) of the output dict, as a JSON string")
    net_hosts: list[str] = Field(description="hostnames the code must reach; [] if none")
    example_input_json: str = Field(description="a realistic example input, as a JSON string")


class Plan(BaseModel):
    reasoning: str
    reuse_workflow: Optional[str] = Field(description="name of an existing workflow that already solves this task, else null")
    workflow_name: str = Field(description="snake_case, generic name for the workflow this task needs")
    workflow_description: str
    workflow_input_schema_json: str = Field(description="JSON Schema of the workflow input, as a JSON string")
    task_input_json: str = Field(description="the concrete workflow input for THIS task, as a JSON string")
    steps: list[StepSpec]
    output_step: str = Field(description="id of the step whose output is the workflow result")
    gaps: list[GapSpec]
