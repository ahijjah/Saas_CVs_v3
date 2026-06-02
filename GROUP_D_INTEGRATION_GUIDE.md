# Group D Integration Guide — Candidate Tags & Talent Pool

## Overview

This guide describes how to complete the Group D implementation by integrating tags and talent pool features into the existing Candidates Workspace and candidate drawer.

## Completed Components

✅ Backend APIs (routers/candidate_tags.py)
✅ Database migrations (071, 072)
✅ Frontend types (types.ts)
✅ API service methods (services/api.ts)
✅ Tag chip component (components/TagChip.tsx)
✅ Talent Pool page (pages/TalentPool.tsx)

## Remaining Integration Tasks

### 1. Update CandidatesWorkspace.tsx

**Task 1.1: Add state variables (after line ~400)**

```typescript
// Tag management
const [allTags, setAllTags] = useState<CandidateTag[]>([]);
const [tagFilter, setTagFilter] = useState<string[]>([]);
const [talentPoolOnly, setTalentPoolOnly] = useState(false);
const [showTagCreator, setShowTagCreator] = useState(false);
const [newTagName, setNewTagName] = useState('');
```

**Task 1.2: Add tag loading effect (after initial candidates effect)**

```typescript
useEffect(() => {
  const loadTags = async () => {
    try {
      const response = await api.listTags();
      setAllTags(response.tags);
    } catch (error) {
      // Silent fail for tags
    }
  };
  loadTags();
}, []);
```

**Task 1.3: Update candidates API call to include tag filter (around line ~1400)**

Add to the candidates query parameters:
```typescript
if (tagFilter.length > 0) {
  params.tag_ids = tagFilter.join(',');
}
if (talentPoolOnly) {
  params.talent_pool_only = 'true';
}
```

**Task 1.4: Add bulk tag actions (after handleBulkMove function)**

```typescript
const handleBulkAddTags = async (tagIds: string[]) => {
  const selectedCandidates = candidates.filter(c => selectedIds.has(c.application_id));
  if (selectedCandidates.length === 0) {
    addToast('No candidates selected', 'warning');
    return;
  }

  let successful = 0, failed = 0;
  for (const candidate of selectedCandidates) {
    try {
      await api.addTagsToApplication(candidate.application_id, tagIds);
      successful++;
    } catch (error) {
      failed++;
    }
  }

  selectedIds.clear();
  await loadCandidates();
  addToast(`${successful} tagged, ${failed} failed`, successful > 0 ? 'success' : 'error');
  setBulkModal(null);
};

const handleBulkRemoveTags = async (tagIds: string[]) => {
  const selectedCandidates = candidates.filter(c => selectedIds.has(c.application_id));
  if (selectedCandidates.length === 0) {
    addToast('No candidates selected', 'warning');
    return;
  }

  let successful = 0, failed = 0;
  for (const candidate of selectedCandidates) {
    for (const tagId of tagIds) {
      try {
        await api.removeTagFromApplication(candidate.application_id, tagId);
        successful++;
      } catch (error) {
        failed++;
      }
    }
  }

  selectedIds.clear();
  await loadCandidates();
  addToast(`${successful} tags removed, ${failed} failed`, successful > 0 ? 'success' : 'error');
  setBulkModal(null);
};
```

**Task 1.5: Add filter UI for tags and talent pool (in filter section)**

```typescript
{/* Tag Filter */}
<div className="border-l pl-3">
  <label className="text-xs font-semibold text-gray-600 mb-2 block">Tags</label>
  <div className="flex flex-wrap gap-1 mb-2">
    {allTags.map(tag => (
      <button
        key={tag.tag_id}
        onClick={() => {
          setTagFilter(prev =>
            prev.includes(tag.tag_id)
              ? prev.filter(id => id !== tag.tag_id)
              : [...prev, tag.tag_id]
          );
        }}
        className={`px-2 py-1 text-xs rounded-full transition-opacity ${
          tagFilter.includes(tag.tag_id)
            ? 'opacity-100 ring-2 ring-offset-1'
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
      onClick={() => setTagFilter([])}
      className="text-xs text-blue-600 hover:underline"
    >
      Clear tags
    </button>
  )}
</div>

{/* Talent Pool Filter */}
<label className="flex items-center gap-2 mt-3">
  <input
    type="checkbox"
    checked={talentPoolOnly}
    onChange={(e) => setTalentPoolOnly(e.target.checked)}
    className="w-4 h-4 rounded"
  />
  <span className="text-xs font-medium text-gray-700">Talent Pool Only</span>
</label>
```

**Task 1.6: Add bulk tag action buttons (in bulk action section)**

```typescript
{bulkModal === 'tags-add' && (
  <div className="modal-overlay">
    <div className="modal-content">
      <h3 className="text-lg font-bold mb-4">Add Tags to {selectedIds.size} Candidates</h3>
      <div className="space-y-2 mb-4 max-h-60 overflow-y-auto">
        {allTags.map(tag => (
          <label key={tag.tag_id} className="flex items-center gap-2">
            <input type="checkbox" className="w-4 h-4" onChange={(e) => {
              // Handle tag selection
            }} />
            <span>{tag.tag_name}</span>
          </label>
        ))}
      </div>
      <div className="flex gap-2">
        <button onClick={() => setBulkModal(null)} className="btn-secondary">Cancel</button>
        <button onClick={() => handleBulkAddTags(selectedTagIds)} className="btn-primary">Add Tags</button>
      </div>
    </div>
  </div>
)}
```

### 2. Update Candidate Drawer Section

**Task 2.1: Add tags display in candidate drawer**

In the candidate detail section (around line ~3500+), add:

```typescript
{/* Tags Section */}
{selectedCandidate && (
  <div className="mt-6 pt-6 border-t">
    <div className="flex items-center justify-between mb-3">
      <h3 className="font-semibold text-gray-900">Tags</h3>
      <button
        onClick={() => setShowTagCreator(true)}
        className="text-xs text-blue-600 hover:underline"
      >
        + Add
      </button>
    </div>
    
    <div className="flex flex-wrap gap-2">
      {candidateTags.map(tag => (
        <TagChip
          key={tag.tag_id}
          tag={tag}
          onRemove={handleRemoveTag}
        />
      ))}
      {candidateTags.length === 0 && (
        <p className="text-xs text-gray-400">No tags yet</p>
      )}
    </div>

    {showTagCreator && (
      <div className="mt-3 p-3 bg-gray-50 rounded-lg">
        <input
          type="text"
          placeholder="New tag name…"
          value={newTagName}
          onChange={(e) => setNewTagName(e.target.value)}
          className="w-full px-3 py-2 border rounded-lg text-sm"
        />
        <div className="flex gap-2 mt-2">
          <button
            onClick={handleCreateAndAddTag}
            className="btn-primary text-xs"
          >
            Create & Add
          </button>
          <button
            onClick={() => {
              setShowTagCreator(false);
              setNewTagName('');
            }}
            className="btn-secondary text-xs"
          >
            Cancel
          </button>
        </div>
      </div>
    )}
  </div>
)}

{/* Talent Pool Section */}
{selectedCandidate && (
  <div className="mt-6 pt-6 border-t">
    <div className="flex items-center justify-between">
      <h3 className="font-semibold text-gray-900">Talent Pool</h3>
      <button
        onClick={handleToggleTalentPool}
        className={`px-3 py-1 rounded-full text-xs font-medium ${
          selectedCandidate.is_talent_pool
            ? 'bg-green-100 text-green-800'
            : 'bg-gray-100 text-gray-700'
        }`}
      >
        {selectedCandidate.is_talent_pool ? 'In Pool' : 'Not in Pool'}
      </button>
    </div>
    
    {selectedCandidate.is_talent_pool && (
      <button
        onClick={handleReuseCandidate}
        className="mt-3 text-xs text-blue-600 hover:underline w-full text-left"
      >
        → Add to another job
      </button>
    )}
  </div>
)}
```

**Task 2.2: Add handler functions**

```typescript
const handleRemoveTag = async (tagId: string) => {
  if (!selectedCandidate) return;
  try {
    await api.removeTagFromApplication(selectedCandidate.application_id, tagId);
    setCandidateTags(prev => prev.filter(t => t.tag_id !== tagId));
    addToast('Tag removed', 'success');
  } catch (error: any) {
    addToast(error.message, 'error');
  }
};

const handleCreateAndAddTag = async () => {
  if (!selectedCandidate || !newTagName.trim()) return;
  try {
    const newTag = await api.createTag(newTagName);
    await api.addTagsToApplication(selectedCandidate.application_id, [newTag.tag_id]);
    setCandidateTags(prev => [...prev, newTag]);
    setAllTags(prev => [...prev, newTag]);
    setNewTagName('');
    setShowTagCreator(false);
    addToast('Tag created and added', 'success');
  } catch (error: any) {
    addToast(error.message, 'error');
  }
};

const handleToggleTalentPool = async () => {
  if (!selectedCandidate) return;
  try {
    const newStatus = !selectedCandidate.is_talent_pool;
    await api.toggleTalentPool(selectedCandidate.application_id, newStatus);
    setSelectedCandidate(prev => prev ? { ...prev, is_talent_pool: newStatus } : null);
    addToast(
      newStatus ? 'Added to talent pool' : 'Removed from talent pool',
      'success'
    );
  } catch (error: any) {
    addToast(error.message, 'error');
  }
};

const handleReuseCandidate = async () => {
  if (!selectedCandidate) return;
  // Show job selector modal and call api.reuseCandidate(id, jobId)
};
```

### 3. Update App.tsx

Add Talent Pool route:

```typescript
import { TalentPool } from './pages/TalentPool';

// In the route definitions:
{ path: '/talent-pool', element: <TalentPool auth={auth} addToast={addToast} /> }
```

### 4. Update Layout.tsx

Add Talent Pool to sidebar:

```typescript
{
  label: 'Talent Pool',
  href: '/talent-pool',
  icon: <TalentPoolIcon />
}
```

### 5. Update CandidatesWorkspace candidate row display

Add talent pool badge to each candidate row in the table:

```typescript
{selectedCandidate.is_talent_pool && (
  <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded-full">
    Talent Pool
  </span>
)}
```

## Translation Updates

Add these translation keys to the `t` object:

```typescript
const t = {
  // ... existing translations ...
  
  // Tags
  tags: 'Tags',
  tagsAddTag: '+ Add Tag',
  tagsNoTags: 'No tags yet',
  tagsCreateNew: 'Create New Tag',
  tagsNewTagPlaceholder: 'New tag name…',
  tagsCreateAndAdd: 'Create & Add',
  tagsBulkAdd: 'Add Tags',
  tagsBulkRemove: 'Remove Tags',
  
  // Talent Pool
  talentPool: 'Talent Pool',
  talentPoolAdd: 'Add to Talent Pool',
  talentPoolRemove: 'Remove from Talent Pool',
  talentPoolIn: 'In Pool',
  talentPoolNotIn: 'Not in Pool',
  talentPoolReuse: '→ Add to another job',
  talentPoolReuseTitle: 'Add Candidate to Another Job',
  
  // Arabic translations
  tagsAr: 'الوسوم',
  // ... etc
};
```

## Testing Checklist

- [ ] Create tag from drawer
- [ ] Add tag to candidate
- [ ] Remove tag from candidate
- [ ] Filter candidates by tag
- [ ] Multiple tag filtering
- [ ] Bulk add tags
- [ ] Bulk remove tags
- [ ] Save view with tag filter persists
- [ ] Add candidate to talent pool
- [ ] Talent pool badge shows in row and drawer
- [ ] Reuse candidate to another job
- [ ] Talent Pool page accessible from sidebar
- [ ] Talent Pool page pagination works
- [ ] No regression in existing features

## Deployment Steps

1. Run migrations 071 and 072
2. Update CandidatesWorkspace.tsx with all additions from Tasks 1.1-1.6
3. Update candidate drawer section with Task 2.1-2.2
4. Update App.tsx and Layout.tsx routing
5. Add translations
6. Test all features using the checklist above

## Notes

- Tag colors are hex values stored in the database
- Tag filtering is client-side applied via API params
- Bulk tag operations iterate per candidate for simplicity
- Talent pool is a boolean flag on applications table
- Candidate reuse creates a new application in the target job
