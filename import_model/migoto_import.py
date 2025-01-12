from .import_utils import *
from .migoto_format import *
from ..utils.collection_utils import *
from ..config.import_model_config import *

from ..utils.obj_utils import ObjUtils
from ..utils.json_utils import JsonUtils

from array import array

import os.path
import itertools
import bpy
import json
import math

from bpy_extras.io_utils import unpack_list, ImportHelper, axis_conversion
from bpy.props import BoolProperty, StringProperty, CollectionProperty
from bpy_extras.io_utils import orientation_helper


def import_shapekeys(mesh, obj, shapekeys):
    if len(shapekeys.keys()) == 0:
        return
    
    # Add basis shapekey
    basis_shapekey = obj.shape_key_add(name='Basis')
    basis_shapekey.interpolation = 'KEY_LINEAR'

    # Set shapekeys to relative 'cause WuWa uses this type
    obj.data.shape_keys.use_relative = True

    # Import shapekeys
    for shapekey_id in shapekeys.keys():
        # Add new shapekey
        shapekey = obj.shape_key_add(name=f'Deform {shapekey_id}')
        shapekey.interpolation = 'KEY_LINEAR'

        # Apply shapekey vertex position offsets to each indexed vertex
        shapekey_data = shapekeys[shapekey_id]
        for vertex_id in range(len(obj.data.vertices)):
            position_offset = shapekey_data[vertex_id]
            shapekey.data[vertex_id].co.x += position_offset[0]
            shapekey.data[vertex_id].co.y += position_offset[1]
            shapekey.data[vertex_id].co.z += position_offset[2]


def import_vertex_groups(mesh, obj, blend_indices, blend_weights,component):
    # 这里注释掉的是旧的不包含Remapped的权重导入
    # assert (len(blend_indices) == len(blend_weights))
    # if blend_indices:
    #     # We will need to make sure we re-export the same blend indices later -
    #     # that they haven't been renumbered. Not positive whether it is better
    #     # to use the vertex group index, vertex group name or attach some extra
    #     # data. Make sure the indices and names match:
    #     num_vertex_groups = max(itertools.chain(*itertools.chain(*blend_indices.values()))) + 1
    #     for i in range(num_vertex_groups):
    #         obj.vertex_groups.new(name=str(i))
    #     for vertex in mesh.vertices:
    #         for semantic_index in sorted(blend_indices.keys()):
    #             for i, w in zip(blend_indices[semantic_index][vertex.index],
    #                             blend_weights[semantic_index][vertex.index]):
    #                 if w == 0.0:
    #                     continue
    #                 obj.vertex_groups[i].add((vertex.index,), w, 'REPLACE')

    assert (len(blend_indices) == len(blend_weights))
    if blend_indices:
        # We will need to make sure we re-export the same blend indices later -
        # that they haven't been renumbered. Not positive whether it is better
        # to use the vertex group index, vertex group name or attach some extra
        # data. Make sure the indices and names match:
        if component is None:
            num_vertex_groups = max(itertools.chain(*itertools.chain(*blend_indices.values()))) + 1
        else:
            num_vertex_groups = max(component.vg_map.values()) + 1
            vg_map = list(map(int, component.vg_map.values()))
        
        # print("num_vertex_groups: " + str(num_vertex_groups))
        # print(vg_map)
        for i in range(num_vertex_groups):
            obj.vertex_groups.new(name=str(i))
        for vertex in mesh.vertices:
            for semantic_index in sorted(blend_indices.keys()):
                for i, w in zip(blend_indices[semantic_index][vertex.index],
                                blend_weights[semantic_index][vertex.index]):
                    if w == 0.0:
                        continue
                    if component is None:
                        obj.vertex_groups[i].add((vertex.index,), w, 'REPLACE')
                    else:
                        obj.vertex_groups[vg_map[i]].add((vertex.index,), w, 'REPLACE')


def import_uv_layers(mesh, obj, texcoords):
    for (texcoord, data) in sorted(texcoords.items()):
        '''
        Nico: 在我们的游戏Mod设计中，TEXCOORD只能有两个分量
        如果出现两个以上，则是自定义数据存储到TEXCOORD使用，所以这里我们只考虑两个分量的情况。

        TEXCOORDS can have up to four components, but UVs can only have two
        dimensions. Not positive of the best way to handle this in general,
        but for now I'm thinking that splitting the TEXCOORD into two sets of
        UV coordinates might work:
        '''
        dim = len(data[0])
        if dim == 4:
            components_list = ('xy', 'zw')
        elif dim == 2:
            components_list = ('xy',)
        else:
            raise Fatal('Unhandled TEXCOORD dimension: %i' % dim)
        cmap = {'x': 0, 'y': 1, 'z': 2, 'w': 3}

        for components in components_list:
            uv_name = 'TEXCOORD%s.%s' % (texcoord and texcoord or '', components)
            if hasattr(mesh, 'uv_textures'):  # 2.79
                mesh.uv_textures.new(uv_name)
            else:  # 2.80
                mesh.uv_layers.new(name=uv_name)
            blender_uvs = mesh.uv_layers[uv_name]

            # Can't find an easy way to flip the display of V in Blender, so
            # add an option to flip it on import & export:
            # 导入时100%必须翻转UV，因为游戏里Dump出来的贴图，就已经是UV翻转的了。
            flip_uv = lambda uv: (uv[0], 1.0 - uv[1])
           
            uvs = [[d[cmap[c]] for c in components] for d in data]
            for l in mesh.loops:
                blender_uvs.data[l.index].uv = flip_uv(uvs[l.vertex_index])


def import_faces_from_ib(mesh, ib):
    mesh.loops.add(len(ib.faces) * 3)
    mesh.polygons.add(len(ib.faces))
    mesh.loops.foreach_set('vertex_index', unpack_list(ib.faces))
    # https://docs.blender.org/api/3.6/bpy.types.MeshPolygon.html#bpy.types.MeshPolygon.loop_start
    mesh.polygons.foreach_set('loop_start', [x * 3 for x in range(len(ib.faces))])
    mesh.polygons.foreach_set('loop_total', [3] * len(ib.faces))


def import_vertices(mesh, vb: VertexBuffer):
    mesh.vertices.add(len(vb.vertices))
    blend_indices = {}
    blend_weights = {}
    texcoords = {}
    shapekeys = {}
    vertex_layers = {}
    use_normals = False
    normals = []

    for elem in vb.layout:
        if elem.InputSlotClass != 'per-vertex':
            continue

        data = tuple(x[elem.name] for x in vb.vertices)
        if elem.name == 'POSITION':
            # Ensure positions are 3-dimensional:
            if len(data[0]) == 4:
                if ([x[3] for x in data] != [1.0] * len(data)):
                    raise Fatal('Positions are 4D')
                    # Nico: Blender暂时不支持4D索引，加了也没用，直接不行就报错，转人工处理。
            positions = [(x[0], x[1], x[2]) for x in data]
            mesh.vertices.foreach_set('co', unpack_list(positions))
        elif elem.name.startswith('COLOR'):
            if len(data[0]) <= 3 or 4 == 4:
                # Nico:实际执行过程中，几乎总会执行这里而不是下面的
                # 即使是原版的也是设置vertex_color_layer_channels = 4 然后这里or进行比较的，所以总是会执行这里的设计。
                # 如果是else下面的执行到会百分百报错的。
                # Either a monochrome/RGB layer, or Blender 2.80 which uses 4
                # channel layers
                mesh.vertex_colors.new(name=elem.name)
                color_layer = mesh.vertex_colors[elem.name].data
                for l in mesh.loops:
                    color_layer[l.index].color = list(data[l.vertex_index]) + [0] * (4 - len(data[l.vertex_index]))
            else:
                mesh.vertex_colors.new(name=elem.name + '.RGB')
                mesh.vertex_colors.new(name=elem.name + '.A')
                color_layer = mesh.vertex_colors[elem.name + '.RGB'].data
                alpha_layer = mesh.vertex_colors[elem.name + '.A'].data
                for l in mesh.loops:
                    color_layer[l.index].color = data[l.vertex_index][:3]
                    alpha_layer[l.index].color = [data[l.vertex_index][3], 0, 0]

        elif elem.name == 'NORMAL':
            use_normals = True
            normals = [(x[0], x[1], x[2]) for x in data]

        elif elem.name in ('TANGENT', 'BINORMAL'):
            pass
            # Nico: 不需要导入TANGENT，因为导出时会重新计算。
            #    # XXX: loops.tangent is read only. Not positive how to handle
            #    # this, or if we should just calculate it when re-exporting.
            #    for l in mesh.loops:
            #        assert(data[l.vertex_index][3] in (1.0, -1.0))
            #        l.tangent[:] = data[l.vertex_index][0:3]
            # print('NOTICE: Skipping import of %s in favour of recalculating on export' % elem.name)
        elif elem.name.startswith('BLENDINDICES'):
            blend_indices[elem.SemanticIndex] = data
        elif elem.name.startswith('BLENDWEIGHT'):
            blend_weights[elem.SemanticIndex] = data
        elif elem.name.startswith('TEXCOORD') and elem.is_float():
            texcoords[elem.SemanticIndex] = data
        elif elem.name.startswith('SHAPEKEY') and elem.is_float():
            shapekeys[elem.SemanticIndex] = data
        else:
            print('NOTICE: Storing unhandled semantic %s %s as vertex layer' % (elem.name, elem.Format))
            vertex_layers[elem.name] = data

    return (blend_indices, blend_weights, texcoords, vertex_layers, use_normals,normals,shapekeys)


def find_texture(texture_prefix, texture_suffix, directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(texture_suffix) and file.startswith(texture_prefix):
                texture_path = os.path.join(root, file)
                return texture_path
    return None


def create_material_with_texture(obj, mesh_name:str, directory:str):
    # Credit to Rayvy
    # Изменим имя текстуры, чтобы оно точно совпадало с шаблоном (Change the texture name to match the template exactly)
    material_name = f"{mesh_name}_Material"
    # texture_name = f"{mesh_name}-DiffuseMap.jpg"

    if "." in mesh_name:
        mesh_name_split = str(mesh_name).split(".")[0].split("-")
    else:
        mesh_name_split = str(mesh_name).split("-")
    
    texture_prefix = mesh_name_split[0] # IB Hash
    if len(mesh_name_split) > 1:
        # new name style need to minus 1.
        texture_suffix = f"{mesh_name_split[1]}-DiffuseMap.tga" # Part Name
    else:
        texture_suffix = "-DiffuseMap.tga"

    # 查找是否存在满足条件的转换好的tga贴图文件

    texture_path = None

    # 查找是否存在满足条件的转换好的tga贴图文件
    texture_path = find_texture(texture_prefix, texture_suffix, directory)
    # 如果不存在，试试查找jpg文件
    if texture_path is None:
        if len(mesh_name_split) > 1:
            texture_suffix = f"{mesh_name_split[1]}-DiffuseMap.jpg"  # Part Name
        else:
            texture_suffix = "-DiffuseMap.jpg"
        # 查找jpg文件，如果这里没找到的话后面也是正常的，但是这里如果找到了就能起到兼容旧版本jpg文件的作用
        texture_path = find_texture(texture_prefix, texture_suffix, directory)

    # 如果还不存在，试试查找png文件
    if texture_path is None:
        if len(mesh_name_split) > 1:
            texture_suffix = f"{mesh_name_split[1]}-DiffuseMap.png"  # Part Name
        else:
            texture_suffix = "-DiffuseMap.png"
        # 查找jpg文件，如果这里没找到的话后面也是正常的，但是这里如果找到了就能起到兼容旧版本jpg文件的作用
        texture_path = find_texture(texture_prefix, texture_suffix, directory)


    # Nico: 这里如果没有检测到对应贴图则不创建材质，也不新建BSDF
    # 否则会造成合并模型后，UV编辑界面选择不同材质的UV会跳到不同UV贴图界面导致无法正常编辑的问题
    if texture_path is not None:
        # Создание нового материала (Create new materials)
        material = bpy.data.materials.new(name=material_name)
        material.use_nodes = True

        # Nico: Currently only support EN and ZH-CN
        # 4.2 简体中文是 "原理化 BSDF" 英文是 "Principled BSDF"
        bsdf = material.node_tree.nodes.get("原理化 BSDF")
        if not bsdf: 
            # 3.6 简体中文是原理化BSDF 没空格
            bsdf = material.node_tree.nodes.get("原理化BSDF")
        if not bsdf:
            bsdf = material.node_tree.nodes.get("Principled BSDF")

        if bsdf:
            # Поиск текстуры (Search for textures)

            if texture_path:
                tex_image = material.node_tree.nodes.new('ShaderNodeTexImage')
                tex_image.image = bpy.data.images.load(texture_path)

                # 因为tga格式贴图有alpha通道，所以必须用CHANNEL_PACKED才能显示正常颜色
                tex_image.image.alpha_mode = "CHANNEL_PACKED"

                material.node_tree.links.new(bsdf.inputs['Base Color'], tex_image.outputs['Color'])

            # Применение материала к мешу (Materials applied to bags)
            if obj.data.materials:
                obj.data.materials[0] = material
            else:
                obj.data.materials.append(material)
    else:
        print(texture_path)


def import_3dmigoto_raw_buffers(operator, context, fmt_path:str, vb_path:str, ib_path:str):
    # get import prefix
    mesh_name = os.path.basename(fmt_path)
    if mesh_name.endswith(".fmt"):
        mesh_name = mesh_name[0:len(mesh_name) - 4]

    # create mesh and obj
    mesh = bpy.data.meshes.new(mesh_name)
    obj = bpy.data.objects.new(mesh.name, mesh)

    # Nico: 虽然每个游戏导入时的坐标不一致，导致模型朝向都不同，但是不在这里修改，而是在后面根据具体的游戏进行扶正
    obj.matrix_world = axis_conversion(from_forward='-Z', from_up='Y').to_4x4()

    # check if .ib .vb file is empty, skip empty import.
    if os.path.getsize(vb_path) == 0 or os.path.getsize(ib_path) == 0:
        return obj

    # create vb and ib class and read data.
    vb = VertexBuffer(open(fmt_path, 'r'))
    vb.parse_vb_bin(open(vb_path, 'rb'))

    ib = IndexBuffer(open(fmt_path, 'r'))
    ib.parse_ib_bin(open(ib_path, 'rb'))

    # Attach the vertex buffer layout to the object for later exporting. Can't
    # seem to retrieve this if attached to the mesh - to_mesh() doesn't copy it:
    obj['3DMigoto:GameTypeName'] = ib.gametypename

    # Nico: 设置默认不重计算TANGNET和COLOR
    obj["3DMigoto:RecalculateTANGENT"] = False
    obj["3DMigoto:RecalculateCOLOR"] = False

    # post process for import data.
    import_faces_from_ib(mesh, ib)

    (blend_indices, blend_weights, texcoords, vertex_layers, use_normals, normals, shapekeys) = import_vertices(mesh, vb)

    import_uv_layers(mesh, obj, texcoords)

    #  metadata.json, if contains then we can import merged vgmap.
    # TimerUtils.Start("Read Metadata")
    component = None
    if bpy.context.scene.dbmt.import_merged_vgmap:
        metadatajsonpath = os.path.join(os.path.dirname(fmt_path),'Metadata.json')
        if os.path.exists(metadatajsonpath):
            # print("鸣潮读取Metadata.json")
            extracted_object = read_metadata(metadatajsonpath)
            fmt_filename = os.path.splitext(os.path.basename(fmt_path))[0]
            if "-" in fmt_filename:
                partname_count = int(fmt_filename.split("-")[1]) - 1
                component = extracted_object.components[partname_count]
    # TimerUtils.End("Read Metadata") # 0:00:00.001490 


    import_vertex_groups(mesh, obj, blend_indices, blend_weights, component)

    import_shapekeys(mesh, obj, shapekeys)

    # Validate closes the loops so they don't disappear after edit mode and probably other important things:
    mesh.validate(verbose=False, clean_customdata=False)  
    mesh.update()
    
    # Nico: 这个方法还必须得在mesh.validate和mesh.update之后调用
    if use_normals:
        mesh.normals_split_custom_set_from_vertices(normals)

    # auto texture 
    create_material_with_texture(obj, mesh_name=mesh_name,directory= os.path.dirname(fmt_path))

    # ZZZ need reset rotation.
    if MainConfig.gamename not in ["GI","HI3","HSR","Game001"]:
        obj.rotation_euler[0] = 0.0  # X轴
        obj.rotation_euler[1] = 0.0  # Y轴
        obj.rotation_euler[2] = 0.0  # Z轴

    # Set scale by user setting when import model.
    scalefactor = bpy.context.scene.dbmt.model_scale
    obj.scale = scalefactor,scalefactor,scalefactor

    if ImportModelConfig.import_flip_scale_x():
        obj.scale.x = obj.scale.x * -1

    # Flush every time export
    bpy.context.view_layer.update()

    # Force flush makes better user experience.
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    return obj



class Import3DMigotoRaw(bpy.types.Operator, ImportHelper):
    """Import raw 3DMigoto vertex and index buffers"""
    bl_idname = "import_mesh.migoto_raw_buffers_mmt"
    bl_label = "导入3Dmigoto的原始Buffer文件"
    bl_description = "导入3Dmigoto格式的 .ib .vb .fmt文件，只需选择.fmt文件即可"

    # new architecture only need .fmt file to locate.
    filename_ext = '.fmt'

    filter_glob: StringProperty(
        default='*.fmt',
        options={'HIDDEN'},
    ) # type: ignore

    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    ) # type: ignore

    def get_vb_ib_paths_from_fmt_prefix(self, filename):
        model_prefix = ImportUtils.get_model_prefix_from_fmt_file(filename).strip()
        # print("model_prefix:" + model_prefix)

        fmt_dir_name = os.path.dirname(filename)

        vb_bin_path = ""
        ib_bin_path = ""
        fmt_path = ""

        if model_prefix == "":
            vb_bin_path = os.path.splitext(filename)[0] + '.vb'
            ib_bin_path = os.path.splitext(filename)[0] + '.ib'
            fmt_path = os.path.splitext(filename)[0] + '.fmt'
        else:
            vb_bin_path = os.path.join(fmt_dir_name, model_prefix + '.vb')
            ib_bin_path = os.path.join(fmt_dir_name, model_prefix + '.ib')
            fmt_path = filename
        
        if not os.path.exists(vb_bin_path):
            raise Fatal('Unable to find matching .vb file for %s' % filename)
        if not os.path.exists(ib_bin_path):
            raise Fatal('Unable to find matching .ib file for %s' % filename)
        if not os.path.exists(fmt_path):
            fmt_path = None
        return (vb_bin_path, ib_bin_path, fmt_path)


    def execute(self, context):
        # 我们需要添加到一个新建的集合里，方便后续操作
        # 这里集合的名称需要为当前文件夹的名称
        dirname = os.path.dirname(self.filepath)

        collection_name = os.path.basename(dirname)
        collection = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(collection)

        # 这里如果用户不选择任何fmt文件，则默认返回读取所有的fmt文件。
        import_filename_list = []
        if len(self.files) == 1:
            if str(self.filepath).endswith(".fmt"):
                import_filename_list.append(self.filepath)
            else:
                for filename in os.listdir(self.filepath):
                    if filename.endswith(".fmt"):
                        import_filename_list.append(filename)
        else:
            for fmtfile in self.files:
                import_filename_list.append(fmtfile.name)


        done = set()
        for fmt_file_name in import_filename_list:
            
            try:
                fmt_file_path = os.path.join(dirname, fmt_file_name)
                (vb_path, ib_path, fmt_path) = self.get_vb_ib_paths_from_fmt_prefix(fmt_file_path)
                if os.path.normcase(vb_path) in done:
                    continue
                done.add(os.path.normcase(fmt_path))

                if fmt_path is not None:
                    # 导入的调用链就从这里开始
                    obj_result = import_3dmigoto_raw_buffers(self, context, fmt_path=fmt_path, vb_path=vb_path, ib_path=ib_path)
                    collection.objects.link(obj_result)
                        
                else:
                    self.report({'ERROR'}, "未找到.fmt文件，无法导入")
            except Fatal as e:
                self.report({'ERROR'}, str(e))
        

        # Select all objects under collection (因为用户习惯了导入后就是全部选中的状态). 
        CollectionUtils.select_collection_objects(collection)

        if ImportModelConfig.import_delete_loose():
            # 用户希望导入后删除松散点
            ObjUtils.selected_obj_delete_loose()

        return {'FINISHED'}


def ImprotFromWorkSpace(self, context):
    import_drawib_aliasname_folder_path_dict = ImportUtils.get_import_drawib_aliasname_folder_path_dict_with_first_match_type()
    print(import_drawib_aliasname_folder_path_dict)

    workspace_collection = CollectionUtils.new_workspace_collection()

    # 读取时保存每个DrawIB对应的GameType名称到工作空间文件夹下面的Import.json，在导出时使用
    draw_ib_gametypename_dict = {}
    for draw_ib_aliasname,import_folder_path in import_drawib_aliasname_folder_path_dict.items():
        tmp_json = ImportUtils.read_tmp_json(import_folder_path)
        work_game_type = tmp_json.get("WorkGameType","")
        draw_ib = draw_ib_aliasname.split("_")[0]
        draw_ib_gametypename_dict[draw_ib] = work_game_type

    save_import_json_path = os.path.join(MainConfig.path_workspace_folder(),"Import.json")

    JsonUtils.SaveToFile(json_dict=draw_ib_gametypename_dict,filepath=save_import_json_path)
    

    # 开始读取模型数据
    for draw_ib_aliasname,import_folder_path in import_drawib_aliasname_folder_path_dict.items():
        import_prefix_list = ImportUtils.get_prefix_list_from_tmp_json(import_folder_path)
        if len(import_prefix_list) == 0:
            self.report({'ERROR'},"当前output文件夹"+draw_ib_aliasname+"中的内容暂不支持一键导入分支模型")
            continue

        draw_ib_collection = CollectionUtils.new_draw_ib_collection(collection_name=draw_ib_aliasname)
        workspace_collection.children.link(draw_ib_collection)

        part_count = 1
        for prefix in import_prefix_list:
            component_name = "Component " + str(part_count)
            component_collection = CollectionUtils.new_component_collection(component_name=component_name)
            defualt_switch_collection = CollectionUtils.new_switch_collection(collection_name="default")

            # combine and verify if path exists.
            vb_bin_path = import_folder_path + "\\" + prefix + '.vb'
            ib_bin_path = import_folder_path + "\\" + prefix + '.ib'
            fmt_path = import_folder_path + "\\" + prefix + '.fmt'

            if not os.path.exists(vb_bin_path):
                raise Fatal('Unable to find matching .vb file for %s' % import_folder_path + "\\" + prefix)
            if not os.path.exists(ib_bin_path):
                raise Fatal('Unable to find matching .ib file for %s' % import_folder_path + "\\" + prefix)
            if not os.path.exists(fmt_path):
                fmt_path = None

            done = set()
            try:
                if os.path.normcase(vb_bin_path) in done:
                    continue
                done.add(os.path.normcase(vb_bin_path))
                if fmt_path is not None:
                    obj_result = import_3dmigoto_raw_buffers(self, context, fmt_path=fmt_path, vb_path=vb_bin_path,
                                                                ib_path=ib_bin_path)
                    defualt_switch_collection.objects.link(obj_result)
                        
                else:
                    self.report({'ERROR'}, "Can't find .fmt file!")
                
                component_collection.children.link(defualt_switch_collection)
                draw_ib_collection.children.link(component_collection)
            except Fatal as e:
                self.report({'ERROR'}, str(e))

            part_count = part_count + 1

    bpy.context.scene.collection.children.link(workspace_collection)

    # Select all objects under collection (因为用户习惯了导入后就是全部选中的状态). 
    CollectionUtils.select_collection_objects(workspace_collection)

    if ImportModelConfig.import_delete_loose():
        # 用户希望导入后删除松散点
        ObjUtils.selected_obj_delete_loose()


class DBMTImportAllFromCurrentWorkSpace(bpy.types.Operator):
    bl_idname = "dbmt.import_all_from_workspace"
    bl_label = "Import all .ib .vb model from current WorkSpace folder."
    bl_description = "一键导入当前工作空间文件夹下所有的DrawIB对应的模型为分支集合架构"

    def execute(self, context):
        if MainConfig.workspacename == "":
            self.report({"ERROR"},"Please select your WorkSpace in DBMT before import.")
        elif not os.path.exists(MainConfig.path_workspace_folder()):
            self.report({"ERROR"},"Please select a correct WorkSpace in DBMT before import")
        else:
            ImprotFromWorkSpace(self,context)
        return {'FINISHED'}