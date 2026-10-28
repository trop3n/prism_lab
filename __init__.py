"""Prism Lab: creative optics FX toolkit for Blender."""

import bpy

from . import datapoints, fx, glass, ripple

modules = (glass, ripple, datapoints, fx)


class PRISM_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Prism Lab"

    def draw(self, context):
        self.layout.label(text="Creative optics FX", icon="LIGHT_SUN")


class PRISM_PT_glass(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Patterned Glass"
    bl_parent_id = "PRISM_PT_main"

    def draw(self, context):
        glass.draw(self.layout, context)


class PRISM_PT_ripple(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Surface Ripple"
    bl_parent_id = "PRISM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        ripple.draw(self.layout, context)


class PRISM_PT_data(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Data Points"
    bl_parent_id = "PRISM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        datapoints.draw(self.layout, context)
        if datapoints.find_overlay(context.object):
            box = self.layout.box()
            fx.draw_glow(box, context)


class PRISM_PT_blur(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Optic Blur"
    bl_parent_id = "PRISM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fx.draw_blur(self.layout, context)


class PRISM_PT_glow(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Prism Lab"
    bl_label = "Glow"
    bl_parent_id = "PRISM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fx.draw_glow(self.layout, context)


panels = (PRISM_PT_main, PRISM_PT_glass, PRISM_PT_ripple, PRISM_PT_data, PRISM_PT_blur, PRISM_PT_glow)


def register():
    for m in modules:
        m.register()
    for p in panels:
        bpy.utils.register_class(p)


def unregister():
    for p in reversed(panels):
        bpy.utils.unregister_class(p)
    for m in reversed(modules):
        m.unregister()
