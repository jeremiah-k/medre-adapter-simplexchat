# Import-safe module entrypoint

Importing the SimpleX module entrypoint does not parse process arguments or
execute operator commands. `python -m medre.adapters.simplexchat` retains its
ordinary CLI behavior. Architecture discovery and other module inspection can
read the adapter without terminating their owning process.
