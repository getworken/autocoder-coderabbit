"""
Agent Types Enum
================

Defines the different types of agents in the system.
"""

from enum import Enum


class AgentType(str, Enum):
    """Types of agents in the autonomous coding system.

    Inherits from str to allow seamless JSON serialization
    and string comparison.

    Usage:
        agent_type = AgentType.CODING
        if agent_type == "coding":  # Works due to str inheritance
            ...
    """

    INITIALIZER = "initializer"
    CODING = "coding"
    TESTING = "testing"

    def __str__(self) -> str:
        """
        Return the enum member's underlying string value.
        
        Returns:
            str: The underlying string value of the enum member.
        """
        return self.value