from pathlib import Path

path = Path("frontend/src/pages/MarketingPlatformWorkspace.jsx")
text = path.read_text(encoding="utf-8")

anchor = 'import DateInput, { isValidISODate } from "../components/DateInput";\n'
component_import = 'import CampaignManagerTable from "../components/marketing/CampaignManagerTable";\n'
if component_import not in text:
    if anchor not in text:
        raise SystemExit("DateInput import anchor not found")
    text = text.replace(anchor, anchor + component_import, 1)

start_marker = '            {activeTab === "campaigns" && (\n'
end_marker = '            {activeTab === "accounts" && (\n'
start = text.find(start_marker)
end = text.find(end_marker, start + 1)
if start < 0 or end < 0:
    raise SystemExit("campaign workspace markers not found")

replacement = '''            {activeTab === "campaigns" && (\n                <CampaignManagerTable\n                    platform={platform}\n                    platformLabel={config.label}\n                    campaigns={data?.campaigns || []}\n                    totals={totals}\n                    pagination={pagination}\n                    page={page}\n                    onPageChange={setPage}\n                    readOnly={data?.policy?.mutations_allowed !== true}\n                />\n            )}\n\n'''

text = text[:start] + replacement + text[end:]
path.write_text(text, encoding="utf-8")
