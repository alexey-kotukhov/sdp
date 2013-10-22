from xml.dom.minidom import parse, parseString
import diameter

class DiameterCommandDef:
  def __init__(self):
    self.application_id = 0
    self.vendor_id = 0
    self.code = 0


class DiameterAVPDef:
  def __init__(self):
    self.mandatory_flag = False
    self.protected_flag = False
    self.vendor_id = 0
    self.code = 0
    self.enum_names = {}
    self.enum_vals = {}

  def addEnum(self, name, val):
    self.enum_names[name] = int(val)
    self.enum_vals[int(val)] = name

  def getEnumValue(self, name):
    if self.enum_names.has_key(name):
        return self.enum_names[name]
    else:
        return 0

  def getEnumName(self, code):
    if self.enum_vals.has_key(code):
        return self.enum_vals[code]
    else:
        return 0


class DiameterDictionary:
  def __init__(self,file):
    self.dom = parse(file)
    self.load()
  def load(self):
    """We only load avps for now
    Add vendors and commands"""
    self.name_to_cmd = {}
    self.name_to_def = {}
    self.def_to_name = {}

    vlist = self.dom.getElementsByTagName('vendor')
    vendors = {}
    for vendor in vlist:
      vendors[vendor.attributes['vendor-id'].value] = int(vendor.attributes['code'].value)

    cmds = self.dom.getElementsByTagName("command")
    for cmd in cmds:
      newCmd = DiameterCommandDef()
      if cmd.parentNode.nodeName == "application":
        newCmd.application_id = int(cmd.parentNode.attributes['id'].value)
      newCmd.vendor_id = vendors[cmd.attributes['vendor-id'].value]
      newCmd.code = int(cmd.attributes['code'].value)
      self.name_to_cmd[cmd.attributes['name'].value] = newCmd

    avps = self.dom.getElementsByTagName("avp")
    for avp in avps:
      newAVP = DiameterAVPDef()
      if avp.attributes.has_key('mandatory') and avp.attributes['mandatory'].value == "must":
        newAVP.mandatory_flag=True
      if avp.attributes.has_key('protected') and avp.attributes['protected'].value == "must":
        newAVP.mandatory_flag=True
      newAVP.code = int(avp.attributes['code'].value)
      if avp.attributes.has_key('vendor-id'):
        newAVP.vendor_id = vendors[avp.attributes['vendor-id'].value]
      #parse enums
      enums = avp.getElementsByTagName("enum")
      for e in enums:
          newAVP.addEnum(e.attributes['name'].value, e.attributes['code'].value)
      self.name_to_def[avp.attributes['name'].value] = newAVP
      self.def_to_name[(newAVP.vendor_id,newAVP.code)] = newAVP

  def getEnumCode(self, avp, name):
      d = self.getAVPDefinition(avp)
      return d.getEnumValue(name)

  def getEnumName(self, avp, code):
      d = self.getAVPDefinition(avp)
      return d.getEnumName(code)
      pass

  def getCommandDefinition(self, name):
      if self.name_to_cmd.has_key(name):
          return self.name_to_cmd[name]
      else:
          return None

  def getCommandRequest(self, stack, name, auth=False, acct=False):
      cmd_def = self.getCommandDefinition(name)
      if cmd_def == None:
          cmd_def = DiameterCommandDef()
      return stack.createRequest(cmd_def.application_id, cmd_def.code, auth, acct)
     
  def getAVPDefinition(self, name):
    if self.name_to_def.has_key(name):
      return self.name_to_def[name]
    else:
      return None

  def getAVPCode(self,name):
      avp_def = self.getAVPDefinition(name)
      if avp_def == None:
          avp_def = DiameterAVPDef()
      return (avp_def.code,avp_def.vendor_id)

  def getAVP(self, name):
      avp_def = self.getAVPDefinition(name)
      if avp_def == None:
          avp_def = DiameterAVPDef()
      ret = diameter.protocol.DiameterAVP()
      ret.setCode(avp_def.code)
      ret.setVendor(avp_def.vendor_id)
      ret.setMandatory(avp_def.mandatory_flag)
      ret.setProtected(avp_def.protected_flag)
      return ret

  def isCommand(self, message, name):
      cmd_def = self.getCommandDefinition(name)
      return cmd_def != None and \
             message.application_id == cmd_def.application_id and \
             message.command_code == cmd_def.code

  def findAVP(self, message_or_avp, name):
      avp_def = self.getAVPDefinition(name)
      if avp_def != None:
          return message_or_avp.findAVP(avp_def.code, avp_def.vendor_id)
      else:
          return None

  def findFirstAVP(self, message_or_avp, *names):
      for name in names:
          avp_def = self.getAVPDefinition(name)
          if avp_def != None:
              message_or_avp = message_or_avp.findFirstAVP(avp_def.code, avp_def.vendor_id)
              if message_or_avp == None:
                  return None
          else:
              return None
      return message_or_avp

