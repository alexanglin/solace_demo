"""Durable receiver-only mission recording and structurally isolated replay.

The service owns capture ordering, export coordination, and replay composition. Transport,
SQLAlchemy repositories, privacy policy, and the versioned recording codec stay behind injected
typed ports owned by their respective packages and decisions.
"""
