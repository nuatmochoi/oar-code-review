from experiment import PORT, ROOT
from recovery_guard import QueuedBudget, base
from freeze_run import verify

frozen = verify()
base.CAP = frozen['new_api_cap_usd']
server = base.Server(('127.0.0.1',PORT),base.Handler)
server.budget = QueuedBudget(cap=base.CAP)
server.token = (ROOT/'broker.token').read_text().strip()
print(f'Durable budget guard ready at {PORT}, remaining budget ${base.CAP:.6f}',flush=True)
server.serve_forever()
