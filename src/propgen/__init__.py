"""PropGen — AI-powered proposal, quote, and estimate generation.

Public entry points:
    - propgen.cli.main                   : Click CLI (`propgen ...`)
    - propgen.mcp_server.server.main    : MCP stdio server

Pluggable base classes (subclass in a downstream persona repo):
    - propgen.ai.classifier.RequestClassifier
    - propgen.ai.drafter.ProposalDrafter
    - propgen.ai.pricer.PricingAssistant
"""

__version__ = "0.1.0"
