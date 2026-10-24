"""Private bounded plan over unchanged A inventory pages; no API pagination."""
from dataclasses import dataclass
from .provider import InventoryPage
from .contracts import Quality


@dataclass(frozen=True)
class WindowInventory:
    pages: tuple[InventoryPage, ...]

    @property
    def items(self): return tuple(item for page in self.pages for item in page.items)
    @property
    def completeness(self): return self.pages[-1].completeness
    @property
    def consistency(self): return self.pages[-1].consistency
    @property
    def ambiguous_refs(self): return tuple(sorted({ref for page in self.pages for ref in page.ambiguous_refs}))
    @property
    def next_cursor(self): return self.pages[-1].next_cursor
    @property
    def terminal(self): return self.pages[-1].terminal


def window_inventory(inventory_id,cut_id,profile,camera,scope,completeness,consistency,
                     items,predecessors,successors,ambiguous,next_cursor,terminal):
    if len(items)>257 or predecessors or successors or next_cursor is not None or terminal is not True:
        raise ValueError('bounded one-observation window plan required')
    pages=[]
    for offset in range(0,max(1,len(items)),128):
        chunk=items[offset:offset+128]; ids={item.item_id for item in chunk}
        final=offset+128>=len(items)
        pages.append(InventoryPage(inventory_id,cut_id,profile,camera,scope,
                     completeness if final else Quality.INCOMPLETE,consistency,chunk,(),(),
                     tuple(ref for ref in ambiguous if ref in ids),None if final else 'window-local-'+str(len(pages)+1),final))
    return WindowInventory(tuple(pages))
