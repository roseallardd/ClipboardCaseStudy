from crm import update_account

# Simulates drift: a rep changed this name back in the CRM
update_account("00191H7JR16471SZ34", {"name": "Bellhaven of Sycamore Ridge"})
print("Reverted Sycamore Ridge name for the demo")