"""Literal single-file search using the existing bounded workspace reader."""
from pydantic import Field, StrictInt
from .filesystem import PathArgs, ReadFile, ReadArgs, WorkspaceTool

class SearchArgs(PathArgs):
    query: str = Field(min_length=1, max_length=1000)
    max_bytes: StrictInt = Field(default=1_000_000, ge=1, le=1_000_000)
    max_matches: StrictInt = Field(default=100, ge=1, le=1000)
    preview_chars: StrictInt = Field(default=500, ge=1, le=2000)

class SearchText(WorkspaceTool):
    name = "workspace.search_text"
    description = "Search one bounded UTF-8 workspace file for literal text, returning line previews."
    arguments_model = SearchArgs

    async def run(self, arguments: SearchArgs):
        read = await ReadFile(self.root).run(ReadArgs(path=arguments.path,max_bytes=arguments.max_bytes))
        matches=[];total=0;preview_truncated=False
        for number,line in enumerate(read['content'].splitlines(),1):
            if arguments.query not in line:continue
            total+=1
            if len(matches)<arguments.max_matches:
                matches.append({'line':number,'text':line[:arguments.preview_chars]})
                preview_truncated=preview_truncated or len(line)>arguments.preview_chars
        return {'path':read['path'],'matches':matches,'matched_lines':total,
                'truncated':total>len(matches),'preview_truncated':preview_truncated,
                'bytes_read':read['bytes']}
