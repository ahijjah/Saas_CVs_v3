# Group D UI Integration Checklist

## Status: ✅ Handlers Complete, ⏳ UI Integration In Progress

**Completed:**
- Tag loading effect
- All handler functions (add/remove/create/toggle/reuse)
- State variables initialized
- Imports added (APIService, CandidateTag, TagChip)

**Remaining:** UI Integration (7 tasks)

---

## TASK 1: Load Candidate Tags When Drawer Opens

**Location:** CandidatesWorkspace.tsx, find where `selectedCandidate` is set

**Code to add** (after selectedCandidate is set):
```typescript
useEffect(() => {
  if (selectedCandidate) {
    loadCandidateTags(selectedCandidate.application_id);
  }
}, [selectedCandidate?.application_id]);
```

**Verification:** When you click a candidate in the list, their tags should load in memory

---

## TASK 2: Add Tag Filter UI to Candidates List

**Location:** CandidatesWorkspace.tsx, find the filter section (around line 3300+)

**Where filters appear:** Search box, workflow dropdown, campaign dropdown, etc.

**Code to add** (in the filter section, after existing filters):

```typescript
{/* Tag Filter */}
<div className="border-l pl-3">
  <label className="text-xs font-semibold text-gray-600 mb-2 block">Tags</label>
  <div className="flex flex-wrap gap-1 mb-2 max-h-24 overflow-y-auto">
    {allTags.map(tag => (
      <button
        key={tag.tag_id}
        onClick={() => {
          setTagFilter(prev =>
            prev.includes(tag.tag_id)
              ? prev.filter(id => id !== tag.tag_id)
              : [...prev, tag.tag_id]
          );
          setPage(1);
        }}
        className={`px-2 py-1 text-xs rounded-full transition-all ${
          tagFilter.includes(tag.tag_id)
            ? 'ring-2 ring-offset-1'
            : 'opacity-60 hover:opacity-80'
        }`}
        style={{ backgroundColor: tag.color || '#3b82f6', color: '#fff' }}
      >
        {tag.tag_name}
      </button>
    ))}
  </div>
  {tagFilter.length > 0 && (
    <button
      onClick={() => {
        setTagFilter([]);
        setPage(1);
      }}
      className="text-xs text-blue-600 hover:underline"
    >
      Clear tags
    </button>
  )}
</div>

{/* Talent Pool Filter */}
<label className="flex items-center gap-2 mt-3 cursor-pointer">
  <input
    type="checkbox"
    checked={talentPoolOnly}
    onChange={(e) => {
      setTalentPoolOnly(e.target.checked);
      setPage(1);
    }}
    className="w-4 h-4 rounded"
  />
  <span className="text-xs font-medium text-gray-700">Talent Pool Only</span>
</label>
```

---

## TASK 3: Update Candidates API Query for Tag Filtering

**Location:** `fetchCandidates()` function, find the API params building

**Current code looks like:**
```typescript
const params: Record<string, string> = {};
if (debouncedSearch) params.search = debouncedSearch;
if (workflowFilter) params.workflow_status = workflowFilter;
// ... more params
```

**Code to add:**
```typescript
if (tagFilter.length > 0) params.tag_ids = tagFilter.join(',');
if (talentPoolOnly) params.talent_pool_only = 'true';
```

**Verification:** When you select tags, the candidate list should filter to only show candidates with those tags

---

## TASK 4: Add Bulk Tag Action Buttons

**Location:** Bulk actions section, find where "Move" and "Assign" buttons appear

**Code to add** (in the bulk actions modal/section):

```typescript
{/* Bulk Tag Actions */}
<div className="flex gap-2 mt-2">
  <button
    onClick={() => {
      setBulkTagMode('add');
      setBulkTagSelection(new Set());
    }}
    className="btn-secondary text-sm"
  >
    + Add Tags
  </button>
  <button
    onClick={() => {
      setBulkTagMode('remove');
      setBulkTagSelection(new Set());
    }}
    className="btn-secondary text-sm"
  >
    - Remove Tags
  </button>
</div>

{/* Tag Selection Modal for Bulk Actions */}
{bulkTagMode && (
  <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
    <div className="bg-white rounded-lg p-6 max-w-md w-full">
      <h3 className="font-bold text-lg mb-4">
        {bulkTagMode === 'add' ? 'Add Tags to' : 'Remove Tags from'} {selectedIds.size} Candidates
      </h3>
      
      <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
        {allTags.map(tag => (
          <label key={tag.tag_id} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={bulkTagSelection.has(tag.tag_id)}
              onChange={(e) => {
                const newSelection = new Set(bulkTagSelection);
                if (e.target.checked) {
                  newSelection.add(tag.tag_id);
                } else {
                  newSelection.delete(tag.tag_id);
                }
                setBulkTagSelection(newSelection);
              }}
              className="w-4 h-4"
            />
            <span
              className="px-2 py-1 rounded-full text-xs text-white"
              style={{ backgroundColor: tag.color || '#3b82f6' }}
            >
              {tag.tag_name}
            </span>
          </label>
        ))}
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => {
            setBulkTagMode(null);
            setBulkTagSelection(new Set());
          }}
          className="btn-secondary flex-1"
        >
          Cancel
        </button>
        <button
          onClick={() => {
            if (bulkTagMode === 'add') {
              handleBulkAddTags();
            } else {
              handleBulkRemoveTags();
            }
          }}
          disabled={bulkTagSelection.size === 0}
          className="btn-primary flex-1 disabled:opacity-50"
        >
          {bulkTagMode === 'add' ? 'Add' : 'Remove'}
        </button>
      </div>
    </div>
  </div>
)}
```

---

## TASK 5: Add Tag Section to Candidate Drawer

**Location:** Candidate drawer section, find where candidate details are displayed (around line 3500+)

**Code to add** (in the candidate detail section, after other candidate info):

```typescript
{/* Tags Section */}
{selectedCandidate && (
  <div className="mt-6 pt-6 border-t">
    <div className="flex items-center justify-between mb-3">
      <h3 className="font-semibold text-gray-900 text-sm">Tags</h3>
      <button
        onClick={() => setShowTagCreator(!showTagCreator)}
        className="text-xs text-blue-600 hover:underline"
      >
        {showTagCreator ? 'Cancel' : '+ Add'}
      </button>
    </div>

    {/* Tag Chips */}
    {candidateTags.length > 0 && (
      <div className="flex flex-wrap gap-2 mb-3">
        {candidateTags.map(tag => (
          <TagChip
            key={tag.tag_id}
            tag={tag}
            onRemove={handleRemoveTagFromCandidate}
          />
        ))}
      </div>
    )}

    {candidateTags.length === 0 && !showTagCreator && (
      <p className="text-xs text-gray-400 mb-3">No tags yet</p>
    )}

    {/* Tag Creator */}
    {showTagCreator && (
      <div className="p-3 bg-gray-50 rounded-lg mb-3">
        <input
          type="text"
          placeholder="Tag name…"
          value={newTagName}
          onChange={(e) => setNewTagName(e.target.value)}
          className="w-full px-2 py-1 border rounded text-sm mb-2"
        />
        
        {/* Existing tags autocomplete */}
        {newTagName && (
          <div className="mb-2 space-y-1 max-h-24 overflow-y-auto">
            {allTags
              .filter(tag => 
                tag.tag_name.toLowerCase().includes(newTagName.toLowerCase()) &&
                !candidateTags.some(t => t.tag_id === tag.tag_id)
              )
              .map(tag => (
                <button
                  key={tag.tag_id}
                  onClick={() => handleAddTagToCandidate(tag.tag_id)}
                  className="block w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-100"
                >
                  {tag.tag_name}
                </button>
              ))}
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={handleCreateAndAddTag}
            disabled={!newTagName.trim()}
            className="flex-1 px-2 py-1 bg-blue-600 text-white text-xs rounded disabled:opacity-50"
          >
            Create & Add
          </button>
          <button
            onClick={() => {
              setShowTagCreator(false);
              setNewTagName('');
            }}
            className="flex-1 px-2 py-1 bg-gray-200 text-gray-700 text-xs rounded"
          >
            Done
          </button>
        </div>
      </div>
    )}
  </div>
)}
```

---

## TASK 6: Add Talent Pool Section to Candidate Drawer

**Location:** Same drawer, after the tags section

**Code to add:**

```typescript
{/* Talent Pool Section */}
{selectedCandidate && (
  <div className="mt-6 pt-6 border-t">
    <div className="flex items-center justify-between">
      <h3 className="font-semibold text-gray-900 text-sm">Talent Pool</h3>
      <button
        onClick={handleToggleTalentPool}
        className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
          (selectedCandidate as any).is_talent_pool
            ? 'bg-green-100 text-green-800 hover:bg-green-200'
            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
        }`}
      >
        {(selectedCandidate as any).is_talent_pool ? '✓ In Pool' : 'Add to Pool'}
      </button>
    </div>

    {(selectedCandidate as any).is_talent_pool && (
      <button
        onClick={() => setShowReuseModal(true)}
        className="mt-3 text-xs text-blue-600 hover:underline font-medium"
      >
        → Add to another job
      </button>
    )}
  </div>
)}

{/* Reuse Candidate Modal */}
{showReuseModal && selectedCandidate && (
  <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
    <div className="bg-white rounded-lg p-6 max-w-md w-full">
      <h3 className="font-bold text-lg mb-4">
        Add {selectedCandidate.candidate_name} to Another Job
      </h3>

      <div className="mb-4">
        <label className="text-xs font-semibold text-gray-600 mb-2 block">
          Target Job
        </label>
        <select
          value={reuseTargetJob}
          onChange={(e) => setReuseTargetJob(e.target.value)}
          className="w-full px-3 py-2 border rounded-lg text-sm"
        >
          <option value="">Select a job…</option>
          {availableJobs.map(job => (
            <option key={job.job_id} value={job.job_id}>
              {job.title}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => {
            setShowReuseModal(false);
            setReuseTargetJob('');
          }}
          className="btn-secondary flex-1"
        >
          Cancel
        </button>
        <button
          onClick={handleReuseCandidate}
          disabled={!reuseTargetJob}
          className="btn-primary flex-1 disabled:opacity-50"
        >
          Add
        </button>
      </div>
    </div>
  </div>
)}
```

**Also add:** Load available jobs on component mount:
```typescript
useEffect(() => {
  if (!auth.token) return;
  apiService.get(WEBHOOK_CONFIG.JOBS_URL, {}, auth.token!)
    .then((data: any) => {
      if (Array.isArray(data)) setAvailableJobs(data);
      else if (data?.jobs) setAvailableJobs(data.jobs);
    })
    .catch(() => {});
}, [auth.token]);
```

---

## TASK 7: Update Saved View Save Handler

**Location:** `handleSaveView()` function

**Current code collects filters, add these lines:**

```typescript
const filters: SavedViewFilters = {
  activeView: activeView as QuickView,
  workflowFilter,
  processingFilter,
  aiResultFilter,
  campaignFilter,
  clientFilter,
  search: debouncedSearch,
  assignedFilter,
  tagFilter,           // ADD THIS
  talentPoolOnly,      // ADD THIS
};
```

---

## TASK 8: Update Search Params Sync

**Location:** `useEffect` that syncs URL params

**Add to the params building:**

```typescript
if (tagFilter.length > 0) {
  setSearchParams(prev => {
    const p = new URLSearchParams(prev);
    p.set('tags', tagFilter.join(','));
    return p;
  });
}

if (talentPoolOnly) {
  setSearchParams(prev => {
    const p = new URLSearchParams(prev);
    p.set('talent_pool', 'true');
    return p;
  });
}
```

---

## TASK 9: Update Candidate Row Display (Optional Badge)

**Location:** Candidate table row rendering

**Add to the row** (optional, for visual indicator):

```typescript
{(candidate as any).is_talent_pool && (
  <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded-full">
    Talent Pool
  </span>
)}
```

---

## Testing Checklist

- [ ] Tags load when candidate drawer opens
- [ ] Tag filter dropdown shows all tenant tags
- [ ] Clicking tag filters candidates correctly
- [ ] Multiple tags can be selected
- [ ] "Talent Pool Only" checkbox filters correctly
- [ ] Tag filter clears when button clicked
- [ ] Bulk Add Tags works (select candidates, add tags)
- [ ] Bulk Remove Tags works
- [ ] Bulk operations show success toast with count
- [ ] Add tag to candidate works inline
- [ ] Create new tag and add works
- [ ] Remove tag from candidate works (click X on chip)
- [ ] Duplicate tags prevented (same tag not addable twice)
- [ ] Talent Pool toggle adds/removes from pool
- [ ] Talent Pool badge shows when candidate in pool
- [ ] Reuse candidate modal opens when "Add to job" clicked
- [ ] Job selector populated with available jobs
- [ ] Reuse candidate creates new application
- [ ] Saved view with tag filters saves and restores
- [ ] URL params include tag filters
- [ ] No TypeScript errors
- [ ] No regressions in existing features (workflow move, assign, etc.)

---

## Deployment Steps

```bash
# 1. Complete all 9 tasks above
# 2. Test thoroughly using checklist

# 3. Build frontend
npm run build

# 4. Deploy
git push
# (Deploy to your environment)
```

---

## Notes

- All handlers are already implemented and tested for errors
- Tag colors are configurable per tag (stored in database)
- Candidate tags load async when drawer opens
- Saved views now persist tag and talent pool filters
- Bulk operations work on selected candidates only
- Reuse creates a NEW application in target job (preserves original)
- No schema changes needed (is_talent_pool already added)

---

## File Modified

- `pages/CandidatesWorkspace.tsx` (~3800 lines)

## Tasks Status

| # | Task | Status |
|---|------|--------|
| 1 | Load candidate tags effect | ⏳ 2 lines |
| 2 | Tag filter UI | ⏳ 35 lines |
| 3 | API query filtering | ⏳ 2 lines |
| 4 | Bulk tag buttons | ⏳ 60 lines |
| 5 | Tag drawer section | ⏳ 80 lines |
| 6 | Talent pool drawer section | ⏳ 60 lines |
| 7 | Saved view handler | ⏳ 2 lines |
| 8 | URL sync | ⏳ 10 lines |
| 9 | Candidate row badge | ⏳ 5 lines |

**Total UI additions: ~250 lines of JSX**

All handlers ready to wire up!
