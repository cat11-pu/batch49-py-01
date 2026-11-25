"""Crafting forge with category substitutes and quality inheritance."""


class ForgeError(Exception):
    """Error raised by the forge."""

    def __init__(self, code, detail=""):
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self):
        if self.detail:
            return "%s: %s" % (self.code, self.detail)
        return self.code


class RecipeSubstitutionForge:
    """Materials, recipes, reservations and crafted items."""

    def __init__(self):
        pass

    def add_material(self, material_id, *, category, quality, qty):
        raise NotImplementedError("add_material")

    def inventory_of(self, material_id):
        raise NotImplementedError("inventory_of")

    def available_of(self, material_id):
        raise NotImplementedError("available_of")

    def register_recipe(self, recipe_id, *, requirements, substitutes=None):
        raise NotImplementedError("register_recipe")

    def preview(self, recipe_id):
        raise NotImplementedError("preview")

    def reserve(self, recipe_id):
        raise NotImplementedError("reserve")

    def release(self, reservation_id):
        raise NotImplementedError("release")

    def craft(self, recipe_id, *, reservation=None):
        raise NotImplementedError("craft")

    def quality_of(self, item_id):
        raise NotImplementedError("quality_of")

    def snapshot(self):
        raise NotImplementedError("snapshot")

    def restore(self, snapshot):
        raise NotImplementedError("restore")
