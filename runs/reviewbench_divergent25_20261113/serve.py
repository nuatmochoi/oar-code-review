from experiment import PORT, ROOT
from recovery_guard import QueuedBudget, base
from freeze_run import verify

verify()
server = base.Server(('127.0.0.1',PORT),base.Handler)
server.budget = QueuedBudget()
server.token = (ROOT/'broker.token').read_text().strip()
print(f'Durable budget guard ready at {PORT}',flush=True)
server.serve_forever()
