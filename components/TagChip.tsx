import React from 'react';
import { CandidateTag } from '../types';

interface TagChipProps {
  tag: CandidateTag;
  onRemove?: (tagId: string) => void;
  disabled?: boolean;
}

const TagChip: React.FC<TagChipProps> = ({ tag, onRemove, disabled = false }) => {
  const bgColor = tag.color || '#3b82f6';
  const textColor = tag.color ? '#ffffff' : '#ffffff';
  const hoverOpacity = disabled ? 'opacity-50' : 'hover:opacity-80';

  return (
    <div
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${hoverOpacity} transition-opacity`}
      style={{
        backgroundColor: bgColor,
        color: textColor,
      }}
    >
      <span>{tag.tag_name}</span>
      {onRemove && !disabled && (
        <button
          onClick={() => onRemove(tag.tag_id)}
          className="ml-0.5 focus:outline-none"
          title="Remove tag"
        >
          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
            <path
              fillRule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
};

export { TagChip };
