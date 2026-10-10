import bpy

# Keymap for Shift+A in Audio Node editor
addon_keymaps = []


def register_node_categories():
    """Register node categories and keymaps for the Add menu (Shift+A)."""
    # Register keymap for Shift+A
    wm = bpy.context.window_manager
    if wm.keyconfigs.addon:
        km = wm.keyconfigs.addon.keymaps.new(name='Node Editor', space_type='NODE_EDITOR')
        kmi = km.keymap_items.new('wm.call_menu', 'A', 'PRESS', shift=True)
        kmi.properties.name = 'AUDIONODES_MT_add'
        addon_keymaps.append((km, kmi))
        print("Audio Nodes: Registered Shift+A keymap for Add menu")
    
    print("Audio Nodes: Node categories registered (use Shift+A or header Add button)")


def unregister_node_categories():
    """Unregister node categories and keymaps."""
    # Remove keymaps
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()


class AUDIONODES_MT_add(bpy.types.Menu):
    """Add node menu for Audio Nodes editor."""
    bl_label = "Add"
    bl_idname = "AUDIONODES_MT_add"
    
    @classmethod
    def poll(cls, context):
        # Only show in node editor with AudioNodeTree
        snode = context.space_data
        return (snode and 
                snode.type == 'NODE_EDITOR' and 
                snode.tree_type == 'AudioNodeTree' and
                snode.node_tree is not None)
    
    def draw(self, context):
        layout = self.layout
        layout.menu("AUDIONODES_MT_add_io")
        layout.menu("AUDIONODES_MT_add_generators")
        layout.menu("AUDIONODES_MT_add_effects")
        layout.menu("AUDIONODES_MT_add_analysis")
        layout.menu("AUDIONODES_MT_add_midi")
        layout.menu("AUDIONODES_MT_add_sequencer")
        layout.menu("AUDIONODES_MT_add_control")


class AUDIONODES_MT_add_io(bpy.types.Menu):
    bl_label = "I/O"
    bl_idname = "AUDIONODES_MT_add_io"
    
    def draw(self, context):
        layout = self.layout
        op = layout.operator("node.add_node", text="Audio Input")
        op.type = "AudioInputNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="Audio Output")
        op.type = "AudioOutputNode"
        op.use_transform = True
        
        layout.separator()
        
        op = layout.operator("node.add_node", text="Value Output")
        op.type = "AudioValueOutputNode"
        op.use_transform = True

        op = layout.operator("node.add_node", text="NLA Scrub")
        op.type = "AudioNLAScrubNode"
        op.use_transform = True


class AUDIONODES_MT_add_generators(bpy.types.Menu):
    bl_label = "Generators"
    bl_idname = "AUDIONODES_MT_add_generators"
    
    def draw(self, context):
        layout = self.layout
        op = layout.operator("node.add_node", text="Oscillator")
        op.type = "AudioOscillatorNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="LFO")
        op.type = "AudioLFONode"
        op.use_transform = True


class AUDIONODES_MT_add_effects(bpy.types.Menu):
    bl_label = "Effects"
    bl_idname = "AUDIONODES_MT_add_effects"
    
    def draw(self, context):
        layout = self.layout
        
        op = layout.operator("node.add_node", text="Filter")
        op.type = "AudioFilterNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="Gain")
        op.type = "AudioGainNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="Delay")
        op.type = "AudioDelayNode"
        op.use_transform = True

        op = layout.operator("node.add_node", text="8ch Mixer")
        op.type = "AudioMixerNode"
        op.use_transform = True

        op = layout.operator("node.add_node", text="Math")
        op.type = "AudioMathNode"
        op.use_transform = True
        
        layout.separator()
        
        op = layout.operator("node.add_node", text="ADSR Envelope")
        op.type = "AudioADSRNode"
        op.use_transform = True


class AUDIONODES_MT_add_analysis(bpy.types.Menu):
    bl_label = "Analysis"
    bl_idname = "AUDIONODES_MT_add_analysis"
    
    def draw(self, context):
        layout = self.layout
        op = layout.operator("node.add_node", text="FFT Analyzer")
        op.type = "AudioFFTAnalyzerNode"
        op.use_transform = True


class AUDIONODES_MT_add_midi(bpy.types.Menu):
    bl_label = "MIDI"
    bl_idname = "AUDIONODES_MT_add_midi"
    
    def draw(self, context):
        layout = self.layout
        
        op = layout.operator("node.add_node", text="MIDI Input")
        op.type = "AudioMIDIInputNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="MIDI Output")
        op.type = "AudioMIDIOutputNode"
        op.use_transform = True
        
        layout.separator()
        
        op = layout.operator("node.add_node", text="MIDI CC")
        op.type = "AudioMIDICCNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="MIDI Clock")
        op.type = "AudioMIDIClockNode"
        op.use_transform = True
        
        layout.separator()
        
        op = layout.operator("node.add_node", text="MIDI Monitor")
        op.type = "AudioMIDIMonitorNode"
        op.use_transform = True


class AUDIONODES_MT_add_sequencer(bpy.types.Menu):
    bl_label = "Sequencer / Sampler"
    bl_idname = "AUDIONODES_MT_add_sequencer"
    
    def draw(self, context):
        layout = self.layout
        
        op = layout.operator("node.add_node", text="Step Sequencer")
        op.type = "AudioSequencerNode"
        op.use_transform = True

        op = layout.operator("node.add_node", text="Bang")
        op.type = "AudioBangNode"
        op.use_transform = True
        
        layout.separator()
        
        op = layout.operator("node.add_node", text="Sampler")
        op.type = "AudioSamplerNode"
        op.use_transform = True
        
        op = layout.operator("node.add_node", text="Drum Sampler")
        op.type = "AudioDrumSamplerNode"
        op.use_transform = True


class AUDIONODES_MT_add_control(bpy.types.Menu):
    bl_label = "Control"
    bl_idname = "AUDIONODES_MT_add_control"

    def draw(self, context):
        layout = self.layout
        for text, idname in (("Trigger", "AudioTriggerNode"),
                             ("Accumulator", "AudioAccumulatorNode"),
                             ("Decay", "AudioDecayNode")):
            op = layout.operator("node.add_node", text=text)
            op.type = idname
            op.use_transform = True

        layout.separator()

        op = layout.operator("node.add_node", text="Camera Cutter")
        op.type = "AudioCameraCutterNode"
        op.use_transform = True
