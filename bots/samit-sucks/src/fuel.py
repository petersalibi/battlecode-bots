"""Fuel readings, for logs only. No decision may branch on remaining(): what earlier code happened
to cost would then change what the bot does (v41 lost 30-50 to identical behaviour through fuel
alone). Optional work is sized from the game state in budget.py instead. Each call costs about
1-2K fuel, so never call it inside loops."""
import _cambc

remaining = _cambc.fuel_remaining
