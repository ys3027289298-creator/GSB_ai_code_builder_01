"""AI-Powered Code Builder.

Modular system for converting natural-language prompts into runnable code,
auto-debugging it, executing it inside a constrained sandbox, tracking
versions, and simulating a deployment pipeline.
"""

from .debugger import AutoDebugger, DebugResult
from .deployment import DeploymentReport, DeploymentSimulator
from .prompt_engine import GeneratedProject, PromptEngine
from .sandbox import SandboxResult, SecureSandbox
from .versioning import VersionStore

__all__ = [
    "AutoDebugger",
    "DebugResult",
    "DeploymentReport",
    "DeploymentSimulator",
    "GeneratedProject",
    "PromptEngine",
    "SandboxResult",
    "SecureSandbox",
    "VersionStore",
]

__version__ = "0.1.0"
