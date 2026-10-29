"""KivyMD 2 needs Material You 3; upstream p4a still defaults to 2.0.10.

Use the publisher's versioned source with an integrity hash. PyProjectRecipe
first uses a matching Android wheel when available, then cross-compiles C++17
from this source. Never substitute a desktop wheel for the Android extension.
"""
from pythonforandroid.recipe import PyProjectRecipe


class MaterialyoucolorRecipe(PyProjectRecipe):
    name = "materialyoucolor"
    version = "3.0.4"
    url = "https://files.pythonhosted.org/packages/eb/23/2e63e7bdfcc1aa7ba955386ad09e3bd8a535a1092d7af501659b63cb6282/materialyoucolor-3.0.4.tar.gz"
    sha256sum = "30b8dde034dce180845b0eaf52f72c5b22cd5b60bd7aff96804febae4e266276"
    stl_lib_name = "c++_shared"
    need_stl_shared = True
    depends = ["pillow"]
    hostpython_prerequisites = ["setuptools", "pybind11>=2.11.0"]
    extra_build_args = []


recipe = MaterialyoucolorRecipe()
