import babysitter
import unittest
import StringIO

class TestLoadConfig(unittest.TestCase):
    
    def setUp(self):
        self.manager = babysitter.Manager()
        
    def _load_config(self, xml):
        xml_as_file = StringIO.StringIO(xml)
        self.manager.load_config(xml_as_file)        
        
    def test_file(self):
        xml = """
        <config>
            <file>
              <location>/tmp</location>
              <timeout>1000</timeout>
            </file>
        </config>
        """
        self._load_config(xml)
        self.assertIsInstance(self.manager._checkers[0], babysitter.File)
        self.assertEqual(self.manager._checkers[0].name, '/tmp')
        self.assertEqual(self.manager._checkers[0].timeout, 1000)
        self.assertTrue(self.manager._checkers[0].state == babysitter.Checker.OK)        

    def test_process(self):
        xml = """
        <config>
            <process>
                <name>init</name>
                <restart_command>sudo service init restart</restart_command>
            </process>
        </config>
        """
        self._load_config(xml)
        self.assertIsInstance(self.manager._checkers[0], babysitter.Process)        
        self.assertEqual(self.manager._checkers[0].name, 'init')
        self.assertEqual(self.manager._checkers[0].restart_command,
                         'sudo service init restart')
        self.assertTrue(self.manager._checkers[0].state == babysitter.Checker.OK)

    def test_email_config(self):
        xml = """
        <config>
            <smtp_server>mail.test.server</smtp_server>
            <email_from>test@email.address</email_from>
            <email_to>another@email.address</email_to>
        </config>
        """
        self._load_config(xml)
        self.assertEqual(self.manager.SMTP_SERVER, 'mail.test.server')
        self.assertEqual(self.manager.EMAIL_FROM, 'test@email.address')
        self.assertEqual(self.manager.EMAIL_TO, 'another@email.address')


        
if __name__ == '__main__':
    unittest.main()