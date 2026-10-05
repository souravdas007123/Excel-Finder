"""Admin sidebar ko workflow ke order me lagata hai (Step 1 -> Step 2 -> ...).

Is file me Django import nahi hai. Ye sirf admin ki app list (dicts) ko dobara jodta hai.
"""

# (group ka title, [("app_label.ModelName", sidebar me dikhne wala naam), ...])
WORKFLOW = [
    ("Step 1 · Scan Your Files", [("fileindex.FileIndex", "1. Scan & Index Files")]),
    ("Step 2 · Search Numbers", [("fileindex.BulkSearch", "2. Bulk Number Search")]),
    ("History", [("fileindex.ScanTask", "Scan History")]),
]

# Baaki apps (neeche dikhte hain) ke naam
RENAME_APPS = {"auth": "Users & Access"}


def build_sidebar(app_list):
    models = {}
    for app in app_list:
        for m in app["models"]:
            models[f"{app['app_label']}.{m['object_name']}"] = m

    used, result = set(), []
    for i, (title, items) in enumerate(WORKFLOW, 1):
        entries = []
        for key, label in items:
            m = models.get(key)   # user ke paas permission nahi hogi toh ye model list me hota hi nahi
            if m:
                entries.append(dict(m, name=label))
                used.add(key)
        if entries:
            result.append({
                "name": title,
                "app_label": f"workflow_step_{i}",
                "app_url": entries[0].get("admin_url") or "#",
                "has_module_perms": True,
                "models": entries,
            })

    # Users, Groups wagairah: sabse neeche, apne purane order me
    for app in app_list:
        rest = [m for m in app["models"] if f"{app['app_label']}.{m['object_name']}" not in used]
        if rest:
            result.append(dict(app, name=RENAME_APPS.get(app["app_label"], app["name"]), models=rest))
    return result